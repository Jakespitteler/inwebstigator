"""Works out when a daily slot falls, and runs tasks on a schedule.

Split out of background_scheduler.py, which keeps the notification run itself.
Nothing here knows about websites, email or the database.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def _slot_on(day: datetime, hour: int, minute: int) -> datetime:
    """The slot on `day`. `day` has to already be in the report tz."""
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def last_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The last slot at or before `now`, in UTC.

    The run guard uses this. A website whose last run is at or after it has
    already been done today.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot > local:
        slot = _slot_on(local - timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


def next_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The next slot after `now`, in UTC.

    Read off the wall clock every time, so nothing drifts and daylight saving
    shifts the run once instead of forever.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot <= local:
        slot = _slot_on(local + timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


async def _run_guarded(task_function: Callable[[], Awaitable[None]]) -> None:
    """Run a task and log whatever it raises instead of letting it escape.

    Otherwise one bad morning kills monitoring for good and says nothing.
    Only catches Exception, so Ctrl-C still works.
    """
    name: str = getattr(task_function, "__name__", repr(task_function))
    try:
        await task_function()
    except Exception:
        log.exception("scheduled task %s failed; carrying on", name)


async def run_loop(interval_seconds: int, task_function: Callable[[], Awaitable[None]]) -> None:
    """Run task_function forever, once every interval_seconds.

    The task's own runtime comes off the wait, so the period stays put. This
    is for the scrape loop. Notifications use run_daily_at instead.
    """
    while True:
        started: float = asyncio.get_running_loop().time()
        await _run_guarded(task_function)
        elapsed: float = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))


async def run_daily_at(
    hour: int,
    minute: int,
    tz: ZoneInfo,
    task_function: Callable[[], Awaitable[None]],
) -> None:
    """Run task_function once a day at a set time.

    Fires once on startup so a process started after the day's slot still
    covers that day. The per-website guard makes that a no-op if it already ran.
    """
    await _run_guarded(task_function)
    while True:
        now: datetime = datetime.now(UTC)
        target: datetime = next_slot_at(now, hour, minute, tz)
        log.info("next notification run at %s", target.astimezone(tz))
        await asyncio.sleep((target - now).total_seconds())
        await _run_guarded(task_function)
