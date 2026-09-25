import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.core.config import config
from app.db.core import get_db_session
from app.db.services.user_service import UserService
from app.models.user_models import UserRead
from app.scanner import scan_user_websites, send_notification

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
db_context = contextmanager(get_db_session)

ADMIN_ID: uuid.UUID = uuid.uuid4()


def _send_health_check_if_no_change(user: UserRead) -> None:
    """Dispatches a health check notification to a user if no email notification
    has been sent within the designated health check threshold.

    Args:
        user (UserRead): The target user to check and notify.
    """
    if user.last_email_at and (datetime.now() - user.last_email_at) > timedelta(days=user.days_between_heath_checks):
        send_notification(user, report="No changes have been found since the last notification")


async def _run_startup_scans_in_background() -> None:
    """Runs missed startup scans and health checks asynchronously in the background
    so they do not block the application startup sequence or UI load.
    """
    # Small grace period to let the window render and server finish initialising
    await asyncio.sleep(1)
    if not config.user_id:
        config.user_id = ADMIN_ID

    with db_context() as session:
        users = UserService(session).get_all()

    for user in users:
        if not user.last_scan_at or (datetime.now() - user.last_scan_at) > timedelta(days=user.days_between_scans):
            logger.info("Missed scan interval detected. Running scan immediately in background...")
            with db_context() as session:
                await scan_user_websites(user)
            _send_health_check_if_no_change(user)

    config.user_id = None


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager that initialises administrative metadata,
    executes catch-up scans on startup for overdue intervals, schedules recurring
    website scans and health checks, and manages the APScheduler lifecycle.

    Args:
        app (FastAPI): The application instance.

    Yields:
        None: Yields control back to FastAPI while the scheduler is active.
    """
    if not config.user_id:
        config.user_id = ADMIN_ID

    with db_context() as session:
        users = UserService(session).get_all()

    for user in users:
        scheduler.add_job(scan_user_websites, "interval", days=user.days_between_scans, args=[user])  # pyright: ignore[reportUnknownMemberType]
        scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
            _send_health_check_if_no_change, "interval", days=user.days_between_heath_checks, args=[user]
        )

    scheduler.start()
    asyncio.create_task(_run_startup_scans_in_background())
    config.user_id = None
    yield
    scheduler.shutdown()
