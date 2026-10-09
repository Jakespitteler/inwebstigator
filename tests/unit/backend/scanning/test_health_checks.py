from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.backend.scanning.health_checks import is_health_check_due, send_due_health_checks
from app.backend.scanning.scan_reports import send_reports_awaiting_email
from app.db.services.recipient_service import RecipientService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate
from app.models.scan_run_models import ScanRunCreate, ScanStatus
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead
from tests.fakes import FakeEmailSender
from tests.unit.backend.email_service.builders import make_website


@pytest.fixture
def mock_db_context(mocker: MockerFixture, session: Session) -> MagicMock:
    """Makes the health checks read from the test database session."""
    mock_context = mocker.MagicMock()
    mock_context.__enter__.return_value = session
    mock_context.__exit__.return_value = False
    return mocker.patch("app.backend.scanning.health_checks.db_context", return_value=mock_context)


def _overdue(recipient: RecipientRead) -> RecipientRead:
    """Returns a copy of the recipient who has not been emailed for longer than their health check interval."""
    return recipient.model_copy(
        update={"last_email_at": datetime.now(UTC) - timedelta(days=recipient.days_between_health_checks + 1)}
    )


def test_health_check_is_not_due_when_never_emailed(test_recipient: RecipientRead) -> None:
    """Tests a recipient who has never been emailed is not sent a health check, as there is no last email
    to count from."""
    recipient_no_email = test_recipient.model_copy(update={"last_email_at": None})

    assert not is_health_check_due(recipient_no_email, datetime.now(UTC) + timedelta(days=365))


@pytest.mark.parametrize(
    ("last_email_at", "now", "expected_due"),
    [
        (datetime(2026, 1, 1, 9, 0).astimezone(), datetime(2026, 1, 9, 9, 0).astimezone(), True),
        (datetime(2026, 1, 8, 8, 0).astimezone(), datetime(2026, 1, 8, 9, 0).astimezone(), False),
        (datetime(2026, 1, 1, 12, 0).astimezone(), datetime(2026, 1, 8, 0, 0).astimezone(), True),
        (datetime(2026, 1, 1, 23, 0).astimezone(), datetime(2026, 1, 8, 8, 0).astimezone(), True),
        (datetime(2026, 1, 1, 8, 0).astimezone(), datetime(2026, 1, 7, 23, 0).astimezone(), False),
    ],
    ids=["overdue", "emailed-an-hour-ago", "start-of-seventh-day", "seventh-day-under-a-week-by-clock", "sixth-day"],
)
def test_health_check_counts_from_start_of_day_last_emailed(
    test_recipient: RecipientRead, last_email_at: datetime, now: datetime, expected_due: bool
) -> None:
    """Tests a weekly health check is due from the seventh day after the day of the last email, so it is
    not delayed a day because the last email went out later in the day than this run."""
    recipient = test_recipient.model_copy(update={"last_email_at": last_email_at, "days_between_health_checks": 7})

    assert is_health_check_due(recipient, now) is expected_due


@pytest.mark.anyio
async def test_health_check_says_when_a_website_needs_attention(
    mock_db_context: MagicMock, test_recipient: RecipientRead, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests a health check does not say all is well while one of the recipient's websites is switched off."""
    overdue = _overdue(test_recipient)
    switched_off = make_website(active=False, deactivated_reason=DeactivationReason.TOO_LARGE, recipients=[overdue])
    mocker.patch.object(RecipientService, "get_all_with_websites", return_value=[overdue])
    mocker.patch.object(WebsiteService, "get_all", return_value=[switched_off])
    mocker.patch("app.backend.scanning.notifications._record_email_sent")

    await send_due_health_checks(email_sender)

    [health_check] = email_sender.sent
    assert health_check.to == overdue.email
    assert health_check.subject == "Health check: 1 of 1 website needs attention"
    assert "too many pages to crawl" in health_check.html_body


@pytest.mark.anyio
async def test_health_checks_read_recipients_fresh_each_time(
    mock_db_context: MagicMock, test_recipient: RecipientRead, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests each run uses the recipient's current state, so an email sent since the last run is respected."""
    just_emailed = test_recipient.model_copy(update={"last_email_at": datetime.now(UTC)})
    mocker.patch.object(
        RecipientService, "get_all_with_websites", side_effect=[[_overdue(test_recipient)], [just_emailed]]
    )
    mock_send_notification = mocker.patch("app.backend.scanning.notifications.send_notification")

    await send_due_health_checks(email_sender)
    await send_due_health_checks(email_sender)

    mock_send_notification.assert_called_once()
    assert mock_db_context.call_count == 2


@pytest.mark.anyio
async def test_health_checks_continue_after_a_failed_send(
    mock_db_context: MagicMock, test_recipient: RecipientRead, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests one recipient's failed send does not stop the remaining recipients being sent their health checks."""
    other_recipient = _overdue(test_recipient).model_copy(update={"email": "other@gmail.com"})
    mocker.patch.object(
        RecipientService, "get_all_with_websites", return_value=[_overdue(test_recipient), other_recipient]
    )
    mock_send_notification = mocker.patch(
        "app.backend.scanning.notifications.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )

    await send_due_health_checks(email_sender)

    sent_to = [call.args[0].to for call in mock_send_notification.call_args_list]
    assert sent_to == [test_recipient.email, other_recipient.email]


@pytest.mark.anyio
async def test_health_checks_only_go_to_recipients_still_on_a_website(
    mock_db_context: MagicMock,
    session: Session,
    test_website: WebsiteRead,
    mocker: MockerFixture,
    email_sender: FakeEmailSender,
) -> None:
    """Tests a recipient removed from every website is no longer sent health checks."""
    recipient_service = RecipientService(session)
    long_ago = RecipientUpdate(last_email_at=datetime.now(UTC) - timedelta(days=30))
    removed_recipient = recipient_service.create(RecipientCreate(email="removed@gmail.com"))
    for recipient in [*test_website.recipients, removed_recipient]:
        recipient_service.update(id=recipient.id, model_update=long_ago)
    mock_send_notification = mocker.patch("app.backend.scanning.notifications.send_notification")

    await send_due_health_checks(email_sender)

    sent_to = [call.args[0].to for call in mock_send_notification.call_args_list]
    assert sent_to == [recipient.email for recipient in test_website.recipients]
    assert removed_recipient.email not in sent_to


def _emailed_days_ago(session: Session, email: str, days: float, days_between_health_checks: float = 7) -> None:
    """Saves that a recipient was last emailed some days ago, with their own health check interval."""
    recipient_service = RecipientService(session)
    recipient = recipient_service.get_by_email(email)
    recipient_service.update(
        id=recipient.id,
        model_update=RecipientUpdate(
            last_email_at=datetime.now(UTC) - timedelta(days=days),
            days_between_health_checks=days_between_health_checks,
        ),
    )


def _add_website(session: Session, url: str, recipient_emails: list[str]) -> WebsiteRead:
    """Adds a website with the given recipients."""
    return WebsiteService(session).create(WebsiteCreate(url=HttpUrl(url), recipient_emails=recipient_emails))


@pytest.mark.anyio
@pytest.mark.usefixtures("mock_db_context", "backend_uses_test_session")
async def test_each_health_check_lists_only_the_recipients_own_websites(
    session: Session, email_sender: FakeEmailSender
) -> None:
    """Tests each recipient's health check covers all of their websites, and none of anyone else's."""
    _add_website(session, "https://fees.example.com", ["both@example.com"])
    _add_website(session, "https://dates.example.com", ["both@example.com", "dates@example.com"])
    for email in ["both@example.com", "dates@example.com"]:
        _emailed_days_ago(session, email, days=8)

    await send_due_health_checks(email_sender)

    by_recipient = {email.to: email for email in email_sender.sent}
    assert by_recipient["both@example.com"].subject == "Health check: monitoring is running for 2 websites"
    assert "fees.example.com" in by_recipient["both@example.com"].html_body
    assert "dates.example.com" in by_recipient["both@example.com"].html_body
    assert by_recipient["dates@example.com"].subject == "Health check: monitoring is running for 1 website"
    assert "fees.example.com" not in by_recipient["dates@example.com"].html_body


@pytest.mark.anyio
@pytest.mark.usefixtures("mock_db_context", "backend_uses_test_session")
async def test_each_recipient_is_sent_health_checks_at_their_own_interval(
    session: Session, email_sender: FakeEmailSender
) -> None:
    """Tests a recipient's own `days_between_health_checks` decides when they are due, so a recipient set to every 3
    days gets one after 4 days without an email, while one on the weekly default does not."""
    _add_website(session, "https://example.com", ["every-3-days@example.com", "weekly@example.com"])
    _emailed_days_ago(session, "every-3-days@example.com", days=4, days_between_health_checks=3)
    _emailed_days_ago(session, "weekly@example.com", days=4, days_between_health_checks=7)

    await send_due_health_checks(email_sender)

    assert [email.to for email in email_sender.sent] == ["every-3-days@example.com"]


@pytest.mark.anyio
@pytest.mark.usefixtures("mock_db_context", "backend_uses_test_session")
async def test_a_health_check_counts_as_contact_so_the_next_run_does_not_send_another(
    session: Session, email_sender: FakeEmailSender
) -> None:
    """Tests sending a health check saves when the recipient was emailed, so the next run does not send another."""
    _add_website(session, "https://example.com", ["someone@example.com"])
    _emailed_days_ago(session, "someone@example.com", days=8)

    await send_due_health_checks(email_sender)
    await send_due_health_checks(email_sender)

    assert [email.to for email in email_sender.sent] == ["someone@example.com"]


@pytest.mark.anyio
@pytest.mark.usefixtures("mock_db_context", "backend_uses_test_session")
async def test_a_scan_report_email_counts_as_contact(session: Session, email_sender: FakeEmailSender) -> None:
    """Tests a recipient just emailed a scan report by the run is not also sent a health check, as the report
    shows monitoring is running."""
    website = _add_website(session, "https://example.com", ["someone@example.com"])
    _emailed_days_ago(session, "someone@example.com", days=8)
    ScanRunService(session).create(
        ScanRunCreate(website_id=website.id, scanned_at=datetime.now(UTC), status=ScanStatus.CONNECTION_ERROR)
    )

    send_reports_awaiting_email(email_sender)
    await send_due_health_checks(email_sender)

    assert [email.subject for email in email_sender.sent] == ["Website update: example.com"]
