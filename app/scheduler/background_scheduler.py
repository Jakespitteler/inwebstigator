"""Background scheduler: drives the notification run, and the scrape once it exists.

    python -m app.scheduler.background_scheduler

The notification run happens once a day at a wall clock time (DAILY_RUN_HOUR /
DAILY_RUN_MINUTE, in REPORT_TIMEZONE) rather than on an interval. An interval
loop waits after the task finishes, so the real period is interval + however
long the run took, and the report walks later every day.

`last_run_at` is stored per website, so a restart doesn't fire a second report
for a day already covered.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import config
from app.db import repository
from app.db.core import SessionLocal
from app.db.schema import DBWebsite
from app.db.services.notification_service import NotificationService
from app.email_sender import notifier

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Scheduling helpers
# ----------------------------------------------------------------------


def _slot_on(day: datetime, hour: int, minute: int) -> datetime:
    """The scheduled instant on `day`, which must already be in the report tz."""
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def last_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The most recent scheduled time at or before `now`, in UTC.

    The run guard compares against this: a website whose last run is at or after
    it has already been covered.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot > local:
        slot = _slot_on(local - timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


def next_slot_at(now: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """The next scheduled time strictly after `now`, in UTC.

    Read off the wall clock each time, so nothing drifts and a daylight saving
    change shifts the run once rather than permanently.
    """
    local: datetime = now.astimezone(tz)
    slot: datetime = _slot_on(local, hour, minute)
    if slot <= local:
        slot = _slot_on(local + timedelta(days=1), hour, minute)
    return slot.astimezone(UTC)


async def _run_guarded(task_function: Callable[[], Awaitable[None]]) -> None:
    """Run a task, logging what it raises instead of letting it escape.

    Otherwise one bad morning ends monitoring for good and says nothing. Only
    Exception is caught, so cancellation and Ctrl-C still work.
    """
    name: str = getattr(task_function, "__name__", repr(task_function))
    try:
        await task_function()
    except Exception:
        log.exception("scheduled task %s failed; carrying on", name)


async def run_loop(interval_seconds: int, task_function: Callable[[], Awaitable[None]]) -> None:
    """Run task_function forever, one go every interval_seconds.

    The task's own duration comes off the wait, so the period stays put. Used
    for the scrape loop; notifications use run_daily_at() instead.
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
    """Run task_function once a day at a wall clock time.

    Fires once on entry so a process started after the day's slot still covers
    that day. The per-website guard makes that a no-op if it already ran.
    """
    await _run_guarded(task_function)
    while True:
        now: datetime = datetime.now(UTC)
        target: datetime = next_slot_at(now, hour, minute, tz)
        log.info("next notification run at %s", target.astimezone(tz))
        await asyncio.sleep((target - now).total_seconds())
        await _run_guarded(task_function)


# ----------------------------------------------------------------------
# The notification run
# ----------------------------------------------------------------------


def collect_changes(session: Session, website: DBWebsite) -> list[notifier.Change]:
    """What the diff finder found for this website since the last run.

    TODO (diff finder): this is the seam it plugs into. It holds both versions
    of a page when it decides one changed, so it can also fill in
    Change.old_text / Change.new_text. Empty until then, so runs stay quiet.
    """
    return []


def run_notifications(session: Session, now: datetime | None = None) -> dict[str, int]:
    """One notification pass over every website. Blocking, call via to_thread.

    Retries parked sends first, then today's report for each website that
    hasn't had one. Returns a count of each action, for logging.
    """
    now = now or datetime.now(UTC)
    service = NotificationService(session)
    tally: dict[str, int] = {}

    sent, queued = service.retry_pending(now=now)
    if sent or queued:
        log.info("retried parked notifications: %d sent, %d still queued", sent, queued)

    cutoff: datetime = last_slot_at(now, config.daily_run_hour, config.daily_run_minute, notifier.report_tz())

    for website in repository.get_list(session, table=DBWebsite, limit=1000):
        if service.has_run_since(website.id, cutoff):
            log.debug("website_id=%s already reported since %s, skipping", website.id, cutoff)
            continue

        changes: list[notifier.Change] = collect_changes(session, website)
        action: str = service.run_for_website(website, changes, now=now)
        tally[action] = tally.get(action, 0) + 1

        if action == "failed":
            # Parked by run_for_website, so they go out on a later retry.
            log.error("notification send failed for %s, %d change(s) parked", website.url, len(changes))
        else:
            log.info("notification run for %s: %s (%d change(s))", website.url, action, len(changes))

    return tally


async def check_notifications() -> None:
    """One notification run, off the event loop.

    smtplib waits up to 30s on a dead server and the database calls are
    synchronous, so running this inline would freeze every other task.
    """

    def _work() -> dict[str, int]:
        session: Session = SessionLocal()
        try:
            tally: dict[str, int] = run_notifications(session)
            session.commit()
            return tally
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    tally: dict[str, int] = await asyncio.to_thread(_work)
    if tally:
        log.info("notification pass complete: %s", tally)


# TODO (scraper): add the scrape loop here once the scraper exists. It goes
# alongside the notification schedule under asyncio.gather():
#
#     await asyncio.gather(
#         run_daily_at(...),
#         run_loop(config.scrape_interval_seconds, check_site),
#     )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    tz: ZoneInfo = notifier.report_tz()
    log.info(
        "scheduler started; daily notification run at %02d:%02d %s (dry_run=%s)",
        config.daily_run_hour,
        config.daily_run_minute,
        tz,
        notifier.DRY_RUN,
    )

    await run_daily_at(
        config.daily_run_hour,
        config.daily_run_minute,
        tz,
        check_notifications,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("scheduler stopped")
