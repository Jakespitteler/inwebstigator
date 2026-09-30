from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.core.config import config
from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientRead
from app.scheduler import (
    _last_contacted_at,  # pyright: ignore[reportPrivateUsage]
    _run_lock,  # pyright: ignore[reportPrivateUsage]
    _run_startup_scans_in_background,  # pyright: ignore[reportPrivateUsage]
    _scan_then_send_health_checks,  # pyright: ignore[reportPrivateUsage]
    _send_due_health_checks,  # pyright: ignore[reportPrivateUsage]
    _send_health_check_if_no_change,  # pyright: ignore[reportPrivateUsage]
    schedule_scans,
    scheduler,
)

# ======================================
# Setup Fixtures & Mocks
# ======================================


@pytest.fixture
def mock_db_context(mocker: MockerFixture, session: Session):
    """Mocks the scheduler's db_context to return the active test database session."""
    mock_context = mocker.MagicMock()
    mock_context.__enter__.return_value = session
    mock_context.__exit__.return_value = False
    return mocker.patch("app.scheduler.db_context", return_value=mock_context)


# ======================================
# Background Helper Function Tests
# ======================================


def test_send_health_check_if_no_change_skipped_when_never_emailed_and_recently_added(
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests that a never-emailed recipient is not sent a health check before a full interval has passed."""
    recipient_no_email = test_recipient.model_copy(update={"last_email_at": None})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_no_email)

    mock_send_notification.assert_not_called()


def test_send_health_check_if_no_change_triggered_when_never_emailed_and_added_long_ago(
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests that a never-emailed recipient gets a health check once a full interval has passed since being added."""
    added_long_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=test_recipient.days_between_health_checks + 1
    )
    recipient_no_email = test_recipient.model_copy(update={"last_email_at": None, "created_at": added_long_ago})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_no_email)

    mock_send_notification.assert_called_once_with(
        recipient_no_email.email,
        report="No changes have been found since the last notification",
    )


def test_last_contacted_at_converts_utc_created_at_to_local_time(test_recipient: RecipientRead):
    """Tests the database's UTC created_at is compared on the same clock as the local last_email_at."""
    created_at_utc = datetime(2026, 1, 1, 12, 0, 0)
    recipient = test_recipient.model_copy(update={"last_email_at": None, "created_at": created_at_utc})

    expected_local = created_at_utc.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert _last_contacted_at(recipient) == expected_local

    emailed_at = datetime(2026, 2, 1, 9, 0, 0)
    assert _last_contacted_at(recipient.model_copy(update={"last_email_at": emailed_at})) == emailed_at


def test_send_health_check_if_no_change_triggered_when_overdue(test_recipient: RecipientRead, mocker: MockerFixture):
    """Tests that a health check notification is sent when recipient.last_email_at exceeds threshold."""
    overdue_email = datetime.now() - timedelta(days=test_recipient.days_between_health_checks + 1)
    recipient_overdue = test_recipient.model_copy(update={"last_email_at": overdue_email})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_overdue)

    mock_send_notification.assert_called_once_with(
        recipient_overdue.email,
        report="No changes have been found since the last notification",
    )


def test_send_health_check_if_no_change_skipped_when_recent(test_recipient: RecipientRead, mocker: MockerFixture):
    """Tests that no health check email is sent if an email was dispatched recently."""
    recent_email = datetime.now() - timedelta(hours=1)
    recipient_recent = test_recipient.model_copy(update={"last_email_at": recent_email})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_recent)

    mock_send_notification.assert_not_called()


def test_send_due_health_checks_reads_recipients_fresh_each_run(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests each run uses the recipient's current state, so an email sent since the last run is respected."""
    overdue = test_recipient.model_copy(
        update={"last_email_at": datetime.now() - timedelta(days=test_recipient.days_between_health_checks + 1)}
    )
    just_emailed = test_recipient.model_copy(update={"last_email_at": datetime.now()})
    mocker.patch.object(RecipientService, "get_all", side_effect=[[overdue], [just_emailed]])
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_due_health_checks()
    _send_due_health_checks()

    mock_send_notification.assert_called_once()
    assert mock_db_context.call_count == 2


def test_send_due_health_checks_continues_after_a_failed_send(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests one recipient's failed send does not stop the remaining recipients being checked."""
    other_recipient = test_recipient.model_copy(update={"email": "other@gmail.com"})
    mocker.patch.object(RecipientService, "get_all", return_value=[test_recipient, other_recipient])
    mock_health_check = mocker.patch(
        "app.scheduler._send_health_check_if_no_change", side_effect=[ConnectionError("smtp down"), None]
    )

    _send_due_health_checks()

    assert mock_health_check.call_count == 2
    mock_health_check.assert_called_with(other_recipient)


@pytest.mark.anyio
async def test_scan_then_send_health_checks_scans_first(mocker: MockerFixture):
    """Tests the scan runs before health checks, so its change emails count as recent contact."""
    calls: list[str] = []
    mocker.patch("app.scheduler.scan_all_websites", side_effect=lambda: calls.append("scan"))
    mocker.patch("app.scheduler._send_due_health_checks", side_effect=lambda: calls.append("health_checks"))

    await _scan_then_send_health_checks()

    assert calls == ["scan", "health_checks"]


@pytest.mark.anyio
async def test_scan_then_send_health_checks_skipped_while_previous_run_in_progress(mocker: MockerFixture):
    """Tests a run does not start while another is still going, so websites are not scanned twice."""
    mock_scan_all = mocker.patch("app.scheduler.scan_all_websites")
    mock_health_checks = mocker.patch("app.scheduler._send_due_health_checks")

    async with _run_lock:
        await _scan_then_send_health_checks()

    mock_scan_all.assert_not_called()
    mock_health_checks.assert_not_called()


@pytest.mark.anyio
async def test_run_startup_scans_in_background(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests background startup task triggers scan_all_websites and checks recipients."""
    mock_scan_all = mocker.patch("app.scheduler.scan_all_websites")
    mock_health_check = mocker.patch("app.scheduler._send_health_check_if_no_change")
    mocker.patch.object(RecipientService, "get_all", return_value=[test_recipient])

    # Fast-forward asyncio.sleep delay inside background job
    mocker.patch("asyncio.sleep", return_value=None)

    await _run_startup_scans_in_background()

    mock_scan_all.assert_called_once()
    mock_db_context.assert_called_once()
    mock_health_check.assert_called_once_with(test_recipient)


# ======================================
# schedule_scans Lifespan Tests
# ======================================


@pytest.mark.anyio
async def test_schedule_scans_lifespan(mocker: MockerFixture):
    """
    Tests lifespan initialisation: adds the scheduled job, dispatches the background
    startup task, and manages lifecycle.
    """
    mock_create_task = mocker.patch("asyncio.create_task", side_effect=lambda coroutine: coroutine.close())

    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")

    app = FastAPI()

    async with schedule_scans(app):
        # A single job covers scans and health checks, so nothing is captured per recipient at startup
        mock_add_job.assert_called_once_with(
            _scan_then_send_health_checks, "interval", hours=config.scheduler_hours_between_scan_checks
        )
        mock_start.assert_called_once()
        mock_create_task.assert_called_once()

    # Shutdown checks
    mock_shutdown.assert_called_once()


def test_scheduled_job_runs_more_often_than_scan_interval():
    """
    Tests the scheduled job runs more often than the default scan interval, so a website whose
    last scan finished just after the previous check is not skipped for a whole extra interval.
    """
    check_interval = timedelta(hours=config.scheduler_hours_between_scan_checks)
    assert check_interval < timedelta(days=config.scheduler_default_days_between_scans)
