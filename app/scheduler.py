import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, time, timedelta

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

    The threshold counts from the start of the day they were last emailed. Emails go out partway
    through a daily run, so counting from the exact time would make the next due run look slightly
    short of the threshold and delay the health check by a whole day.

    Args:
        recipient (RecipientRead): The target recipient to check and notify.
    """
    if recipient.last_email_at and (
        datetime.now() - datetime.combine(recipient.last_email_at.date(), time.min)  # Start of the day
    ) >= timedelta(days=recipient.days_between_health_checks):
        send_notification(
            recipient.email,
            report="No changes have been found since the last notification",
            subject="Health Check",
        )


async def _scan_then_send_health_checks() -> None:
    """Scans the websites that are due, then sends the health checks that are due.

    Recipients are read after the scan, on every run, so a change notification sent by the scan
    counts as recent contact, and newly added recipients and changed intervals are picked up.
    """
    await scan_all_websites()

    with db_context() as session:
        recipients = RecipientService(session).get_all()
    for recipient in recipients:
        try:
            _send_health_check_if_no_change(recipient)
        except Exception:
            logger.exception(f"Failed to send health check to {recipient.email}")


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager that schedules the recurring website scans and health
    checks, and manages the APScheduler lifecycle.

    Args:
        app (FastAPI): The application instance.

    Yields:
        None: Yields control back to FastAPI while the scheduler is active.
    """

    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        _scan_then_send_health_checks,
        "interval",
        days=config.scheduler_minimum_days_between_scans,
        next_run_time=datetime.now() + timedelta(seconds=1),
        misfire_grace_time=None,
        id="scan_then_send_health_checks",
        replace_existing=True,
    )

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
