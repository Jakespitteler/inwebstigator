import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.core.config import config
from app.db.core import get_db_session
from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientRead
from app.scanner import scan_all_websites, send_notification

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
db_context = contextmanager(get_db_session)


def _send_health_check_if_no_change(recipient: RecipientRead) -> None:
    """Dispatches a health check notification to a recipient if no email notification
    has been sent within the designated health check threshold.

    Args:
        recipient (RecipientRead): The target recipient to check and notify.
    """
    if recipient.last_email_at and (datetime.now() - recipient.last_email_at) > timedelta(
        days=recipient.days_between_health_checks
    ):
        send_notification(recipient.email, report="No changes have been found since the last notification")


async def _run_startup_scans_in_background() -> None:
    """Runs missed startup scans and health checks asynchronously in the background

    so they do not block the application startup sequence or UI load.
    """
    # Small grace period to let the window render and server finish initialising
    await asyncio.sleep(1)
    await scan_all_websites()

    with db_context() as session:
        recipients = RecipientService(session).get_all()
    for recipient in recipients:
        _send_health_check_if_no_change(recipient)


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

    # Check for due websites more often than the shortest scan interval. Each website is only
    # scanned once its own days_between_scans has elapsed, so checking at that same interval
    # would find it just short of due (its last scan finished after the previous check) and
    # skip it until the following run.
    scheduler.add_job(scan_all_websites, "interval", hours=config.scheduler_hours_between_scan_checks)  # pyright: ignore[reportUnknownMemberType]

    with db_context() as session:
        recipients = RecipientService(session).get_all()
    for recipient in recipients:
        scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
            _send_health_check_if_no_change,
            "interval",
            days=recipient.days_between_health_checks,
            args=[recipient],
        )

    scheduler.start()
    asyncio.create_task(_run_startup_scans_in_background())
    yield
    scheduler.shutdown()
