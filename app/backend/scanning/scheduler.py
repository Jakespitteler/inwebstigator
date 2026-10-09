from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.scanning.all_websites_scan import latest_check_time, scan_all_websites
from app.backend.scanning.health_checks import send_due_health_checks
from app.core.config import config

SCAN_JOB_ID: str = "scan_then_send_health_checks"
CATCH_UP_JOB_ID: str = "catch_up_on_missed_checks"
scheduler = AsyncIOScheduler()


async def scan_then_send_health_checks(email_sender: EmailSender | None = None) -> None:
    """Scans the websites that are due, then sends the health checks that are due.

    The health checks go out after the scan, so a change email the scan sent counts as recent contact. If the scan
    fails, no health checks are sent, so nobody is told nothing has changed when it was not checked.

    Args:
        email_sender: Sends the emails. Defaults to the configured mail server.
    """
    sender: EmailSender = email_sender or get_email_sender()
    await scan_all_websites(email_sender=sender)
    await send_due_health_checks(sender)


def next_scheduled_check() -> datetime | None:
    """Gets when the scheduler next checks which websites are due a scan.

    Returns:
        The time of the next check, or None if automatic scans are not running.
    """
    job = scheduler.get_job(SCAN_JOB_ID)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return job.next_run_time if job else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan that runs the scans and health checks on a schedule while the app is running.

    The checks are at the same times every day (8am and 8pm by default, see `latest_check_time`), whenever the app
    was started. One more check runs shortly after startup, to catch up on a check missed while the app or the
    computer was off (e.g. this morning's), which only scans the websites that missed it. A check that is late (e.g.
    because the computer was asleep) still happens.

    Args:
        app: The application instance.

    Yields:
        Control back to FastAPI while the scheduler is running.
    """
    started_at: datetime = datetime.now(UTC)
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        func=scan_then_send_health_checks,
        trigger="interval",
        days=config.scheduler_minimum_days_between_scans,
        start_date=latest_check_time(started_at),  # Lines the checks up with the scan time, e.g. 8am and 8pm
        misfire_grace_time=None,
        id=SCAN_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        func=scan_then_send_health_checks,
        trigger="date",
        run_date=started_at + timedelta(seconds=1),
        misfire_grace_time=None,
        id=CATCH_UP_JOB_ID,
        replace_existing=True,
    )

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
