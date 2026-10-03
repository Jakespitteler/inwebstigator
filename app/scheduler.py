import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta

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
_run_lock = asyncio.Lock()


def _last_contacted_at(recipient: RecipientRead) -> datetime:
    """Returns when a recipient last heard from us, as a naive local datetime.

    A recipient who has never been emailed counts from when they were added, so their
    first health check arrives one full interval later rather than never.

    Args:
        recipient (RecipientRead): The recipient to check.
    """
    if recipient.last_email_at:
        return recipient.last_email_at
    # created_at is set by the database in UTC, while last_email_at is written as naive local time
    return recipient.created_at.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)


def _send_health_check_if_no_change(recipient: RecipientRead) -> None:
    """Dispatches a health check notification to a recipient if no email notification
    has been sent within the designated health check threshold.

    Args:
        recipient (RecipientRead): The target recipient to check and notify.
    """
    if (datetime.now() - _last_contacted_at(recipient)) > timedelta(days=recipient.days_between_health_checks):
        send_notification(
            recipient.email,
            report="No changes have been found since the last notification",
            subject="Health Check",
        )


def _send_due_health_checks() -> None:
    """Sends a health check to every recipient who is due one.

    Recipients are read from the database on every run, so emails sent since the last
    run, newly added recipients and changed intervals are all taken into account.
    """
    with db_context() as session:
        recipients = RecipientService(session).get_all()
    for recipient in recipients:
        try:
            _send_health_check_if_no_change(recipient)
        except Exception:
            logger.exception(f"Failed to send health check to {recipient.email}")


async def _scan_then_send_health_checks() -> None:
    """Scans the websites that are due, then sends any health checks that are due.

    The scan runs first so a change notification it sends counts as recent contact,
    and a recipient is not told nothing has changed just before being told what did.

    A run is skipped while another is still in progress, so a long startup scan cannot
    overlap the next scheduled run and scan the same websites twice.
    """
    if _run_lock.locked():
        logger.info("Skipping scheduled run as the previous one is still in progress.")
        return

    async with _run_lock:
        await scan_all_websites()
        _send_due_health_checks()


async def _run_startup_scans_in_background() -> None:
    """Runs missed startup scans and health checks asynchronously in the background

    so they do not block the application startup sequence or UI load.
    """
    # Small grace period to let the window render and server finish initialising
    await asyncio.sleep(1)
    await _scan_then_send_health_checks()


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
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        _scan_then_send_health_checks, "interval", hours=config.scheduler_hours_between_scan_checks
    )

    scheduler.start()
    asyncio.create_task(_run_startup_scans_in_background())
    yield
    scheduler.shutdown()
