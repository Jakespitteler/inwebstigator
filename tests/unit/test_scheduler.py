from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.db.services.user_service import UserService
from app.models.user_models import UserRead
from app.scheduler import (
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


def test_send_health_check_if_no_change_triggered_when_never_emailed(
    mock_db_context: MagicMock,
    test_user: UserRead,
    mocker: MockerFixture,
):
    """Tests that a health check notification is sent when user.last_email_at is None."""
    user_no_email = test_user.model_copy(update={"last_email_at": None})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(user_no_email)

    mock_db_context.assert_called_once()
    mock_send_notification.assert_called_once_with(
        mock_db_context.return_value.__enter__.return_value,
        user_no_email,
        report="No changes have been found since the last notification",
    )


def test_send_health_check_if_no_change_triggered_when_overdue(
    mock_db_context: MagicMock,
    test_user: UserRead,
    mocker: MockerFixture,
):
    """Tests that a health check notification is sent when user.last_email_at exceeds threshold."""
    overdue_email = datetime.now() - timedelta(days=test_user.days_between_heath_checks + 1)
    user_overdue = test_user.model_copy(update={"last_email_at": overdue_email})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(user_overdue)

    mock_send_notification.assert_called_once()


def test_send_health_check_if_no_change_skipped_when_recent(
    test_user: UserRead,
    mocker: MockerFixture,
):
    """Tests that no health check email is sent if an email was dispatched recently."""
    recent_email = datetime.now() - timedelta(hours=1)
    user_recent = test_user.model_copy(update={"last_email_at": recent_email})
    mock_send_notification = mocker.patch("app.scheduler.send_notification")

    _send_health_check_if_no_change(user_recent)

    mock_send_notification.assert_not_called()


# ======================================
# schedule_scans Lifespan Tests
# ======================================


@pytest.mark.anyio
async def test_schedule_scans_lifespan(mock_db_context: MagicMock, test_user: UserRead, mocker: MockerFixture):
    """Tests lifespan initialisation: sets admin config, processes overdue scans, registers jobs, and manages"""
    mocker.patch.object(UserService, "get_all", return_value=[test_user])
    mock_scan_user = mocker.patch("app.scheduler.scan_user_websites")
    mock_health_check = mocker.patch("app.scheduler._send_health_check_if_no_change")

    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")

    app = FastAPI()

    async with schedule_scans(app):
        # Startup checks
        mock_scan_user.assert_called_once_with(mock_db_context.return_value.__enter__.return_value, test_user)
        mock_health_check.assert_called_once_with(test_user)

        # Job registrations (1 scan job + 1 health check job)
        assert mock_add_job.call_count == 2
        mock_start.assert_called_once()

    # Shutdown checks
    mock_shutdown.assert_called_once()


@pytest.mark.anyio
async def test_schedule_scans_skips_startup_scan_if_recent(test_user: UserRead, mocker: MockerFixture):
    """Tests that startup scans are skipped for users scanned within the defined interval."""
    recent_user = test_user.model_copy(update={"last_scan_at": datetime.now()})
    mocker.patch.object(UserService, "get_all", return_value=[recent_user])
    mock_scan_user = mocker.patch("app.scheduler.scan_user_websites")
    mocker.patch("app.scheduler._send_health_check_if_no_change")
    mocker.patch.object(scheduler, "add_job")
    mocker.patch.object(scheduler, "start")
    mocker.patch.object(scheduler, "shutdown")

    app = FastAPI()

    async with schedule_scans(app):
        mock_scan_user.assert_not_called()
