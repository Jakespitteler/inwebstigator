from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientRead
from app.scheduler import (
    _run_startup_scans_in_background,  # pyright: ignore[reportPrivateUsage]
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


def test_send_health_check_if_no_change_skipped_when_never_emailed(
    test_recipient: RecipientRead,
    mocker: MockerFixture,
):
    """Tests that a health check notification is skipped when recipient.last_email_at is None."""
    recipient_no_email = test_recipient.model_copy(update={"last_email_at": None})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(recipient_no_email)

    mock_send_notification.assert_not_called()


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
async def test_schedule_scans_lifespan(test_recipient: RecipientRead, mocker: MockerFixture):
    """
    Tests lifespan initialisation: fetches recipients, adds scheduled jobs, dispatches
    background tasks, and manages lifecycle.
    """
    mocker.patch.object(RecipientService, "get_all", return_value=[test_recipient])
    mock_create_task = mocker.patch("asyncio.create_task")

    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")

    app = FastAPI()

    async with schedule_scans(app):
        # Job registrations: 1 default scan job + 1 recipient health check job
        assert mock_add_job.call_count == 2
        mock_start.assert_called_once()
        mock_create_task.assert_called_once()

    # Shutdown checks
    mock_shutdown.assert_called_once()
