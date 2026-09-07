import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Config
from app.db.schema import DBWebsite
from app.email_sender import notifier
from app.scheduler import background_scheduler
from app.scheduler.background_scheduler import check_notifications, run_notifications
from app.scheduler.utils import last_slot_at, next_slot_at, run_loop

PERTH = ZoneInfo("Australia/Perth")


#  configuration
# The scheduler's settings moved into the project-wide Config, so they are
# validated in the same place as everything else rather than in a model of
# their own.


def test_config_accepts_positive_intervals():
    config = Config(scrape_interval_seconds=5)

    assert config.scrape_interval_seconds == 5


def test_config_rejects_a_zero_interval():
    with pytest.raises(ValidationError):
        Config(scrape_interval_seconds=0)


def test_config_rejects_an_impossible_run_hour():
    with pytest.raises(ValidationError):
        Config(daily_run_hour=24)


def test_client_to_is_split_into_addresses():
    config = Config(client_to="a@example.com, b@example.com")

    assert config.client_to == ["a@example.com", "b@example.com"]


def test_client_to_ignores_blank_entries():
    config = Config(client_to="a@example.com,,  ,b@example.com")

    assert config.client_to == ["a@example.com", "b@example.com"]


def test_client_to_accepts_a_list_unchanged():
    # Only .env is comma separated. A list passed in shouldn't be re-split.
    assert Config(client_to=["a@example.com"]).client_to == ["a@example.com"]


#  wall clock scheduling
# The daily run is scheduled against the clock rather than by adding 24 hours
# to the last run, so it can't drift later every day.


def test_next_slot_is_later_today_when_the_time_is_still_ahead():
    now = datetime(2026, 8, 27, 3, 0, tzinfo=PERTH)

    assert next_slot_at(now, 6, 0, PERTH).astimezone(PERTH) == datetime(2026, 8, 27, 6, 0, tzinfo=PERTH)


def test_next_slot_rolls_to_tomorrow_once_the_time_has_passed():
    now = datetime(2026, 8, 27, 6, 30, tzinfo=PERTH)

    assert next_slot_at(now, 6, 0, PERTH).astimezone(PERTH) == datetime(2026, 8, 28, 6, 0, tzinfo=PERTH)


def test_next_slot_is_always_strictly_in_the_future():
    # Landing exactly on the slot must not schedule a zero second sleep and
    # spin the run twice.
    now = datetime(2026, 8, 27, 6, 0, tzinfo=PERTH)

    assert next_slot_at(now, 6, 0, PERTH) > now


def test_the_slot_does_not_drift_across_a_run_that_took_time():
    # The whole point of wall clock scheduling: a slow run doesn't push
    # tomorrow's report later.
    first = next_slot_at(datetime(2026, 8, 27, 3, 0, tzinfo=PERTH), 6, 0, PERTH)
    after_a_long_run = next_slot_at(first + timedelta(minutes=47), 6, 0, PERTH)

    assert after_a_long_run.astimezone(PERTH).time() == first.astimezone(PERTH).time()


def test_last_slot_is_earlier_today_once_the_time_has_passed():
    now = datetime(2026, 8, 27, 9, 0, tzinfo=PERTH)

    assert last_slot_at(now, 6, 0, PERTH).astimezone(PERTH) == datetime(2026, 8, 27, 6, 0, tzinfo=PERTH)


def test_last_slot_falls_back_to_yesterday_before_the_time():
    now = datetime(2026, 8, 27, 3, 0, tzinfo=PERTH)

    assert last_slot_at(now, 6, 0, PERTH).astimezone(PERTH) == datetime(2026, 8, 26, 6, 0, tzinfo=PERTH)


#  run_loop robustness
# Written with asyncio.run() inside sync tests rather than async test
# functions, so this needs no pytest-asyncio.


def test_run_loop_survives_a_task_that_raises(caplog):
    # One bad morning -- a network blip, a database hiccup -- must not end
    # monitoring for good. Before this, the exception unwound out of the loop
    # and nothing restarted it.
    runs = 0

    async def flaky():
        nonlocal runs
        runs += 1
        if runs == 2:
            raise RuntimeError("site unreachable")

    async def drive():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run_loop(0.01, flaky), timeout=0.2)

    asyncio.run(drive())

    assert runs > 2, "loop stopped at the exception instead of carrying on"


def test_run_loop_logs_the_failure_rather_than_swallowing_it(caplog):
    async def always_fails():
        raise RuntimeError("site unreachable")

    async def drive():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run_loop(0.01, always_fails), timeout=0.05)

    with caplog.at_level(logging.ERROR):
        asyncio.run(drive())

    assert "always_fails" in caplog.text
    assert "site unreachable" in caplog.text


def test_run_loop_is_still_cancellable():
    # Only Exception is caught, so cancellation and Ctrl-C keep working.
    async def noop():
        pass

    async def drive():
        task = asyncio.create_task(run_loop(0.01, noop))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())


def test_run_loop_subtracts_the_task_duration_from_the_wait():
    # Sleeping the full interval *after* the task makes the real period
    # interval + duration, which is the drift this removes.
    starts = []

    async def slow():
        starts.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.04)

    async def drive():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run_loop(0.05, slow), timeout=0.28)

    asyncio.run(drive())

    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert gaps, "task never ran twice"
    # Period stays ~0.05 rather than stretching to ~0.09.
    assert max(gaps) < 0.075, f"loop drifted: gaps {gaps}"


#  the notification run


def test_run_notifications_reports_each_website(session: Session, test_website: DBWebsite, monkeypatch):
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: True)

    tally = run_notifications(session, now=datetime(2026, 8, 27, 6, 0, tzinfo=UTC))

    # No diff finder yet, so there is nothing to report and the run is quiet.
    assert tally == {"nothing": 1}


def test_a_website_is_not_reported_twice_in_one_day(session: Session, test_website: DBWebsite, monkeypatch):
    # A restart part-way through the day must not fire a second report.
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: True)
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)

    run_notifications(session, now=now)
    second = run_notifications(session, now=now + timedelta(hours=1))

    assert second == {}, "the same day was reported twice after a restart"


def test_a_new_day_is_reported_again(session: Session, test_website: DBWebsite, monkeypatch):
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: True)

    run_notifications(session, now=datetime(2026, 8, 27, 9, 0, tzinfo=UTC))
    next_day = run_notifications(session, now=datetime(2026, 8, 28, 9, 0, tzinfo=UTC))

    assert next_day == {"nothing": 1}


def test_check_notifications_keeps_the_blocking_work_off_the_event_loop(monkeypatch):
    # smtplib waits up to 30s on an unresponsive server, and the database calls
    # are synchronous too. Run inline, that freezes every other task --
    # including the scrape loop once it's added.
    monkeypatch.setattr(
        background_scheduler,
        "run_notifications",
        lambda session, now=None: (time.sleep(0.2), {"digest": 1})[1],
    )
    monkeypatch.setattr(background_scheduler, "get_db_session", _null_session)

    ticks = []

    async def ticker():
        for _ in range(4):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    async def drive():
        await asyncio.gather(ticker(), check_notifications())

    asyncio.run(drive())

    spread = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
    assert spread < 0.15, f"event loop stalled for {spread:.2f}s during the send"


def test_a_failed_send_is_logged_as_an_error(session: Session, test_website: DBWebsite, monkeypatch, caplog):
    # notify() returns "failed" precisely so the caller can notice. If nobody
    # looks at it, a dropped report is invisible.
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: False)
    monkeypatch.setattr(
        background_scheduler,
        "collect_changes",
        lambda session, website: [notifier.Change(type="PAGE_ADDED", url="https://example.edu.au/new")],
    )

    with caplog.at_level(logging.ERROR):
        tally = run_notifications(session, now=datetime(2026, 8, 27, 6, 0, tzinfo=UTC))

    assert tally == {"failed": 1}
    assert "send failed" in caplog.text
    assert "parked" in caplog.text


@contextlib.contextmanager
def _null_session():
    """Stand-in for get_db_session(), for the off-the-loop timing test.

    Nothing to commit, and run_notifications is stubbed out anyway.
    """
    yield None
