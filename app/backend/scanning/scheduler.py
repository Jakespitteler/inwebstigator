from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.scanning.all_websites_scan import scan_all_websites
from app.backend.scanning.health_checks import send_due_health_checks
from app.backend.scanning.scan_schedule import latest_check_time
from app.core.config import config

SCAN_JOB_ID: str = "scan_then_send_health_checks"
CATCH_UP_JOB_ID: str = "catch_up_on_missed_checks"
ONE_HOUR: timedelta = timedelta(hours=1)
ONE_DAY: timedelta = timedelta(days=1)
scheduler = AsyncIOScheduler()


def _check_hours(time_between_checks: timedelta) -> list[int] | None:
    """Works out the hours of the day the checks are at, when the time between them splits a day into whole hours.

    For example 8am and 8pm for checks 12 hours apart starting at 8am.

    Args:
        time_between_checks: The time from one check to the next.

    Returns:
        The hours, earliest first, or None if the checks do not fall on the same hours every day (e.g. 1.5 days apart).
    """
    splits_day_into_hours: bool = (
        time_between_checks >= ONE_HOUR
        and time_between_checks % ONE_HOUR == timedelta(0)
        and ONE_DAY % time_between_checks == timedelta(0)
    )
    if not splits_day_into_hours:
        return None
    hours_between_checks: int = time_between_checks // ONE_HOUR
    first_hour: int = config.scheduler_scan_time.hour
    return sorted((first_hour + check * hours_between_checks) % 24 for check in range(ONE_DAY // time_between_checks))


def _check_timing(started_at: datetime) -> dict[str, Any]:
    """Works out how the scheduler times the regular checks.

    When the checks fall on the same hours every day (e.g. 8am and 8pm), they are set by the clock, so they stay at
    those times when daylight saving starts or ends. Otherwise they are counted on from the scan time, which moves
    them by an hour when the clocks change, until the app is next started.

    Args:
        started_at: When the app started.

    Returns:
        The scheduler's trigger and its options, which APScheduler takes as untyped keyword arguments.
    """
    time_between_checks: timedelta = timedelta(days=config.scheduler_minimum_days_between_scans)
    check_hours: list[int] | None = _check_hours(time_between_checks)
    if check_hours is not None:
        scan_time = config.scheduler_scan_time
        return {
            "trigger": "cron",
            "hour": ",".join(str(hour) for hour in check_hours),
            "minute": scan_time.minute,
            "second": scan_time.second,
        }
    return {
        "trigger": "interval",
        "days": config.scheduler_minimum_days_between_scans,
        "start_date": latest_check_time(started_at),  # Lines the checks up with the scan time
    }


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
    was started, including after daylight saving starts or ends (see `_check_timing`). One more check runs shortly
    after startup, to catch up on a check missed while the app or the computer was off (e.g. this morning's), which
    only scans the websites that missed it. A check that is late (e.g. because the computer was asleep) still happens.

    Args:
        app: The application instance.

    Yields:
        Control back to FastAPI while the scheduler is running.
    """
    started_at: datetime = datetime.now(UTC)
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        func=scan_then_send_health_checks,
        **_check_timing(started_at),
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
