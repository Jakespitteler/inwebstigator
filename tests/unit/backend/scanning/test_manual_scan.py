from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.backend.scanning.manual_scan import scan_website_now
from app.backend.scanning.scan_queue import ScanQueue
from app.core.errors import NotFoundError, ReportNotEmailedError, ScanAlreadyQueuedError
from app.db.services.recipient_service import RecipientService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunCreate, ScanRunRead
from app.models.website_models import WebsiteCreate, WebsiteRead
from tests.fakes import FakeEmailSender

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("backend_uses_test_session")]


def _add_website(session: Session, url: str, recipient_emails: list[str]) -> WebsiteRead:
    """Adds a website with the given recipients."""
    return WebsiteService(session).create(WebsiteCreate(url=HttpUrl(url), recipient_emails=recipient_emails))


def _scan_finds(session: Session, mocker: MockerFixture, website: WebsiteRead, new_pages: list[str]) -> ScanRunRead:
    """Makes the scan record a scan of the website in its history that found the given new pages, as a real scan
    would, without loading anything online."""
    scan_run = ScanRunService(session).create(
        ScanRunCreate(
            website_id=website.id,
            scanned_at=datetime.now(UTC),
            changes=[ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(page)) for page in new_pages],
        )
    )
    mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=scan_run)
    return scan_run


async def test_manual_scan_emails_the_recipients_and_the_extra_address_once_each(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests "Run Scan Now" emails its report to each of the website's recipients and to the extra address asked for,
    each only once even when the extra address is already a recipient, and returns the report."""
    website = _add_website(session, "https://example.com", ["first@example.com", "second@example.com"])
    _scan_finds(session, mocker, website, ["https://example.com/new"])

    report = await scan_website_now(website.url, email_sender, extra_email="second@example.com")

    assert sorted(email.to for email in email_sender.sent) == ["first@example.com", "second@example.com"]
    assert {email.subject for email in email_sender.sent} == {"Manual scan: example.com"}
    assert report is not None and "https://example.com/new" in report
    assert all(email.html_body == report for email in email_sender.sent)


async def test_manual_scan_also_emails_an_extra_address_that_is_not_a_recipient(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests the extra address is emailed as well as the website's own recipients."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    _scan_finds(session, mocker, website, ["https://example.com/new"])

    await scan_website_now(website.url, email_sender, extra_email="manager@example.com")

    assert [email.to for email in email_sender.sent] == ["recipient@example.com", "manager@example.com"]


async def test_manual_scan_records_its_report_as_sent_and_the_recipients_as_emailed(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests an emailed "Run Scan Now" report is recorded as sent, so the next scheduled run does not send it again,
    and its recipients as emailed, so their health checks count from now."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    _scan_finds(session, mocker, website, ["https://example.com/new"])
    before = datetime.now(UTC)

    await scan_website_now(website.url, email_sender)

    assert ScanRunService(session).get_awaiting_email() == []
    last_email_at = RecipientService(session).get_by_email("recipient@example.com").last_email_at
    assert last_email_at is not None and before <= last_email_at <= datetime.now(UTC)


async def test_manual_scan_that_finds_nothing_sends_no_email_but_records_the_scan_time(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests a "Run Scan Now" that found nothing returns no report and emails nobody, but still saves when the
    website was scanned."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    _scan_finds(session, mocker, website, [])
    before = datetime.now(UTC)

    assert await scan_website_now(website.url, email_sender, extra_email="manager@example.com") is None

    assert email_sender.sent == []
    last_scan_at = WebsiteService(session).get(website.id).last_scan_at
    assert last_scan_at is not None and before <= last_scan_at <= datetime.now(UTC)


async def test_manual_scan_of_a_website_without_recipients_returns_its_report_without_emailing(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests a dashboard-only website (no recipients, and no extra address given) still gets its report shown,
    without any email being sent."""
    website = _add_website(session, "https://example.com", [])
    _scan_finds(session, mocker, website, ["https://example.com/new"])

    report = await scan_website_now(website.url, email_sender)

    assert report is not None and "https://example.com/new" in report
    assert email_sender.sent == []


async def test_a_manual_report_a_scheduled_run_already_emailed_only_goes_to_the_extra_address(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests a "Run Scan Now" report that a scheduled run emailed to the website's recipients while the scan was
    finishing is not emailed to them again, but still goes to the extra address asked for."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    scan_run = _scan_finds(session, mocker, website, ["https://example.com/new"])
    ScanRunService(session).mark_emailed([scan_run.id], emailed_at=datetime.now(UTC))  # By the scheduled run

    await scan_website_now(website.url, email_sender, extra_email="extra@example.com")

    assert [email.to for email in email_sender.sent] == ["extra@example.com"]


async def test_manual_scan_keeps_its_report_when_the_email_fails(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests a report that could not be emailed raises, so the user is told, and is kept to send with the next
    scheduled run."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    scan_run = _scan_finds(session, mocker, website, ["https://example.com/new"])
    mocker.patch.object(email_sender, "send", side_effect=ConnectionError("No internet"))

    with pytest.raises(ReportNotEmailedError):
        await scan_website_now(website.url, email_sender)

    assert [waiting.id for waiting in ScanRunService(session).get_awaiting_email()] == [scan_run.id]


async def test_manual_scan_of_a_website_that_is_not_monitored_is_refused(
    mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests "Run Scan Now" refuses a website that is not monitored, without scanning it."""
    mock_scan_website = mocker.patch("app.backend.scanning.manual_scan.scan_website")

    with pytest.raises(NotFoundError):
        await scan_website_now(HttpUrl("https://not-monitored.example.com/"), email_sender)

    mock_scan_website.assert_not_called()


async def test_manual_scan_of_a_website_already_being_scanned_is_refused(
    session: Session, mocker: MockerFixture, empty_scan_queue: ScanQueue, email_sender: FakeEmailSender
) -> None:
    """Tests "Run Scan Now" for a website already queued or being scanned is refused, emails nobody and does not
    record a scan time, as the website was not scanned."""
    website = _add_website(session, "https://example.com", ["recipient@example.com"])
    mocker.patch.dict(empty_scan_queue._scans, {str(website.url): mocker.Mock()})  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ScanAlreadyQueuedError):
        await scan_website_now(website.url, email_sender)

    assert email_sender.sent == []
    assert WebsiteService(session).get(website.id).last_scan_at is None
