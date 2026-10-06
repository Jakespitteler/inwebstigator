import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.core.config import config
from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientCreate, RecipientRead
from app.models.website_models import WebsiteRead
from app.scheduler import (
    SCAN_JOB_ID,
    _scan_then_send_health_checks,  # pyright: ignore[reportPrivateUsage]
    _send_health_check_if_no_change,  # pyright: ignore[reportPrivateUsage]
    restart_scan_countdown,
    schedule_scans,
    scheduler,
)

HEALTH_CHECK_REPORT = "No changes have been found since the last notification"

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


def _freeze_now(mocker: MockerFixture, now: datetime) -> None:
    """Makes datetime.now() in the scheduler return a fixed time, leaving the rest of datetime unchanged."""
    mocker.patch("app.scheduler.datetime", wraps=datetime).now.return_value = now


# ======================================
# _send_health_check_if_no_change Tests
# ======================================


def test_send_health_check_if_no_change_skipped_when_never_emailed(
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests a recipient who has never been emailed is not sent a health check, as there is no last email
    to count from."""
    recipient_no_email = test_recipient.model_copy(update={"last_email_at": None})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_no_email)

    mock_send_notification.assert_not_called()


@pytest.mark.parametrize(
    ("last_email_at", "now", "expected_sent"),
    [
        (datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 9, 9, 0), True),
        (datetime(2026, 1, 8, 8, 0), datetime(2026, 1, 8, 9, 0), False),
        (datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 8, 0, 0), True),
        (datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 8, 8, 0), True),
        (datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 7, 23, 0), False),
    ],
    ids=["overdue", "emailed-an-hour-ago", "start-of-seventh-day", "seventh-day-under-a-week-by-clock", "sixth-day"],
)
def test_send_health_check_if_no_change_counts_from_start_of_day_last_emailed(
    test_recipient: RecipientRead,
    mocker: MockerFixture,
    last_email_at: datetime,
    now: datetime,
    expected_sent: bool,
):
    """Tests a weekly health check is sent from the seventh day after the day of the last email, so it is
    not delayed a day because the last email went out later in the day than this run."""
    recipient = test_recipient.model_copy(update={"last_email_at": last_email_at, "days_between_health_checks": 7})
    _freeze_now(mocker, now)
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient)

    expected_calls = [call(recipient.email, report=HEALTH_CHECK_REPORT, subject="Health Check")]
    assert mock_send_notification.call_args_list == (expected_calls if expected_sent else [])


# ======================================
# _scan_then_send_health_checks Tests
# ======================================


@pytest.mark.anyio
async def test_scan_then_send_health_checks_reads_recipients_after_scanning(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests recipients are read after the scan, so its change emails count as recent contact and a
    recipient is not told nothing has changed just after being told what did."""
    calls: list[str] = []

    def read_recipients() -> list[RecipientRead]:
        calls.append("read_recipients")
        return [test_recipient]

    mocker.patch("app.scheduler.scan_all_websites", side_effect=lambda: calls.append("scan"))
    mocker.patch.object(RecipientService, "get_all_with_websites", side_effect=read_recipients)
    mocker.patch(
        "app.scheduler._send_health_check_if_no_change",
        side_effect=lambda recipient: calls.append("health_check"),  # pyright: ignore[reportUnknownLambdaType]
    )

    await _scan_then_send_health_checks()

    assert calls == ["scan", "read_recipients", "health_check"]


@pytest.mark.anyio
async def test_scan_then_send_health_checks_reads_recipients_fresh_each_run(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests each run uses the recipient's current state, so an email sent since the last run is respected."""
    overdue = test_recipient.model_copy(
        update={"last_email_at": datetime.now() - timedelta(days=test_recipient.days_between_health_checks + 1)}
    )
    just_emailed = test_recipient.model_copy(update={"last_email_at": datetime.now()})
    mocker.patch("app.scheduler.scan_all_websites")
    mocker.patch.object(RecipientService, "get_all_with_websites", side_effect=[[overdue], [just_emailed]])
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    await _scan_then_send_health_checks()
    await _scan_then_send_health_checks()

    mock_send_notification.assert_called_once()
    assert mock_db_context.call_count == 2


@pytest.mark.anyio
async def test_scan_then_send_health_checks_continues_after_a_failed_send(
    mock_db_context: MagicMock,
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests one recipient's failed send does not stop the remaining recipients being checked."""
    other_recipient = test_recipient.model_copy(update={"email": "other@gmail.com"})
    mocker.patch("app.scheduler.scan_all_websites")
    mocker.patch.object(RecipientService, "get_all_with_websites", return_value=[test_recipient, other_recipient])
    mock_health_check = mocker.patch(
        "app.scheduler._send_health_check_if_no_change", side_effect=[ConnectionError("smtp down"), None]
    )

    await _scan_then_send_health_checks()

    assert mock_health_check.call_count == 2
    mock_health_check.assert_called_with(other_recipient)


@pytest.mark.anyio
async def test_scan_then_send_health_checks_only_checks_recipients_still_on_a_website(
    mock_db_context: MagicMock,
    session: Session,
    test_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a recipient removed from every website is no longer sent health checks."""
    removed_recipient = RecipientService(session).create(RecipientCreate(email="removed@gmail.com"))
    mocker.patch("app.scheduler.scan_all_websites")
    mock_health_check = mocker.patch("app.scheduler._send_health_check_if_no_change")

    await _scan_then_send_health_checks()

    checked_emails = [call.args[0].email for call in mock_health_check.call_args_list]
    assert [recipient.email for recipient in test_website.recipients] == checked_emails
    assert removed_recipient.email not in checked_emails


@pytest.mark.anyio
async def test_scan_then_send_health_checks_sends_no_health_checks_when_the_scan_fails(
    mock_db_context: MagicMock,
    mocker: MockerFixture,
):
    """Tests recipients are not told nothing has changed when the scan itself failed."""
    mocker.patch("app.scheduler.scan_all_websites", side_effect=RuntimeError("database unavailable"))
    mock_health_check = mocker.patch("app.scheduler._send_health_check_if_no_change")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await _scan_then_send_health_checks()

    mock_health_check.assert_not_called()


# ======================================
# restart_scan_countdown Tests
# ======================================


def test_restart_scan_countdown_gives_the_scan_job_a_new_interval_from_now(mocker: MockerFixture):
    """Tests the scan job gets a fresh interval trigger, which next fires one full interval from now."""
    mocker.patch.object(scheduler, "get_job", return_value=mocker.Mock())
    mock_reschedule_job = mocker.patch.object(scheduler, "reschedule_job")

    restart_scan_countdown()

    mock_reschedule_job.assert_called_once_with(
        SCAN_JOB_ID, trigger="interval", days=config.scheduler_minimum_days_between_scans
    )


def test_restart_scan_countdown_does_nothing_when_automatic_scans_are_off(mocker: MockerFixture):
    """Tests nothing is rescheduled when there is no scan job, as automatic scans are not running."""
    mocker.patch.object(scheduler, "get_job", return_value=None)
    mock_reschedule_job = mocker.patch.object(scheduler, "reschedule_job")

    restart_scan_countdown()

    mock_reschedule_job.assert_not_called()


# ======================================
# schedule_scans Lifespan Tests
# ======================================


@pytest.mark.anyio
async def test_schedule_scans_lifespan(mocker: MockerFixture):
    """Tests lifespan initialisation: one job covering scans and health checks, first run shortly after
    startup to catch up on anything missed while the app was closed, and the scheduler's lifecycle."""
    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")
    started_at = datetime.now()

    async with schedule_scans(FastAPI()):
        mock_add_job.assert_called_once()
        assert mock_add_job.call_args.args == (_scan_then_send_health_checks, "interval")
        job_options = dict(mock_add_job.call_args.kwargs)
        next_run_time = job_options.pop("next_run_time")
        assert started_at < next_run_time <= datetime.now() + timedelta(seconds=1)
        assert job_options == {
            "days": config.scheduler_minimum_days_between_scans,
            "misfire_grace_time": None,  # a late run (e.g. after the computer slept) still happens
            "id": "scan_then_send_health_checks",
            "replace_existing": True,  # a restarted lifespan does not add a duplicate job
        }
        mock_start.assert_called_once()
        mock_shutdown.assert_not_called()

    mock_shutdown.assert_called_once()


@pytest.mark.anyio
async def test_schedule_scans_shuts_down_scheduler_when_app_errors(mocker: MockerFixture):
    """Tests the scheduler is still shut down if the app raises while it is running."""
    mocker.patch.object(scheduler, "add_job")
    mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")

    with pytest.raises(RuntimeError, match="app crashed"):
        async with schedule_scans(FastAPI()):
            raise RuntimeError("app crashed")

    mock_shutdown.assert_called_once()


@pytest.mark.anyio
async def test_schedule_scans_runs_job_shortly_after_startup(mocker: MockerFixture):
    """Tests a real scheduler accepts the job's options and runs it shortly after startup."""
    job_ran = asyncio.Event()

    async def fake_scan_then_send_health_checks() -> None:
        job_ran.set()

    mocker.patch("app.scheduler.scheduler", AsyncIOScheduler())
    mocker.patch("app.scheduler._scan_then_send_health_checks", fake_scan_then_send_health_checks)

    async with schedule_scans(FastAPI()):
        await asyncio.wait_for(job_ran.wait(), timeout=5)


def test_default_scan_interval_is_not_below_scheduler_interval():
    """Tests new websites are not given a scan interval shorter than the scheduler runs, which would scan
    them less often than they are set to."""
    assert config.scheduler_default_days_between_scans >= config.scheduler_minimum_days_between_scans
