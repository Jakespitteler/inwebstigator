from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.core.config import config
from app.scanning.all_websites_scan import scan_all_websites
from app.scanning.health_checks import send_due_health_checks

SCAN_JOB_ID: str = "scan_then_send_health_checks"
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


def restart_scan_countdown() -> None:
    """Restarts the countdown to the next scheduled check, e.g. after every website has just been scanned on demand.

    The job is given a new interval trigger, which first fires one interval from now. Does nothing if automatic
    scans are not running.
    """
    if scheduler.get_job(SCAN_JOB_ID) is None:  # pyright: ignore[reportUnknownMemberType]
        return
    scheduler.reschedule_job(  # pyright: ignore[reportUnknownMemberType]
        job_id=SCAN_JOB_ID,
        trigger="interval",
        days=config.scheduler_minimum_days_between_scans,
    )


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan that runs the scans and health checks on a schedule while the app is running.

    The first run is shortly after startup, to catch up on anything missed while the app was closed. A run that is
    late (e.g. because the computer was asleep) still happens.

    Args:
        app: The application instance.

    Yields:
        Control back to FastAPI while the scheduler is running.
    """
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        func=scan_then_send_health_checks,
        trigger="interval",
        days=config.scheduler_minimum_days_between_scans,
        next_run_time=datetime.now() + timedelta(seconds=1),
        misfire_grace_time=None,
        id=SCAN_JOB_ID,
        replace_existing=True,
    )

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
