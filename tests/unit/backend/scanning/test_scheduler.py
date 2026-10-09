import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI
from pytest_mock import MockerFixture

from app.backend.scanning.all_websites_scan import latest_check_time
from app.backend.scanning.scheduler import (
    next_scheduled_check,
    scan_then_send_health_checks,
    schedule_scans,
    scheduler,
)
from app.core.config import config


async def _do_nothing() -> None:
    """Stands in for the scheduled scans and health checks, so a real scheduler can run without scanning anything."""


@pytest.mark.anyio
async def test_scan_then_send_health_checks_sends_health_checks_after_scanning(mocker: MockerFixture) -> None:
    """Tests health checks go out after the scan, so its change emails count as recent contact and a
    recipient is not told nothing has changed just after being told what did."""
    calls: list[str] = []

    def record(step: str) -> Callable[..., None]:
        return lambda *args, **kwargs: calls.append(step)

    mocker.patch("app.backend.scanning.scheduler.scan_all_websites", side_effect=record("scan"))
    mocker.patch("app.backend.scanning.scheduler.send_due_health_checks", side_effect=record("health_checks"))

    await scan_then_send_health_checks()

    assert calls == ["scan", "health_checks"]


@pytest.mark.anyio
async def test_scan_then_send_health_checks_sends_no_health_checks_when_the_scan_fails(mocker: MockerFixture) -> None:
    """Tests recipients are not told nothing has changed when the scan itself failed."""
    mocker.patch("app.backend.scanning.scheduler.scan_all_websites", side_effect=RuntimeError("database unavailable"))
    mock_send_due_health_checks = mocker.patch("app.backend.scanning.scheduler.send_due_health_checks")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await scan_then_send_health_checks()

    mock_send_due_health_checks.assert_not_called()


@pytest.mark.anyio
async def test_scheduled_checks_are_at_the_scan_time_whenever_the_app_started(mocker: MockerFixture) -> None:
    """Tests the next scheduled check is the next 8am or 8pm on the computer's clock, rather than counted from when
    the app started, as a real scheduler sees it."""
    mocker.patch.object(config, "scheduler_scan_time", time(8, 0))
    mocker.patch.object(config, "scheduler_minimum_days_between_scans", 0.5)
    mocker.patch("app.backend.scanning.scheduler.scheduler", AsyncIOScheduler())
    mocker.patch("app.backend.scanning.scheduler.scan_then_send_health_checks", _do_nothing)
    started_at = datetime.now(UTC)

    async with schedule_scans(FastAPI()):
        next_check = next_scheduled_check()

    assert next_check is not None
    assert next_check == latest_check_time(started_at) + timedelta(hours=12)
    assert next_check.astimezone().time() in (time(8, 0), time(20, 0))


@pytest.mark.anyio
async def test_schedule_scans_lifespan(mocker: MockerFixture) -> None:
    """Tests lifespan initialisation: one job for the scans and health checks at the scheduled checks, one more
    shortly after startup to catch up on a check missed while the app was closed, and the scheduler's lifecycle."""
    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")
    started_at = datetime.now(UTC)

    async with schedule_scans(FastAPI()):
        scheduled_checks, catch_up = mock_add_job.call_args_list
        assert scheduled_checks.args == catch_up.args == ()

        scheduled_options = dict(scheduled_checks.kwargs)
        assert scheduled_options.pop("start_date") == latest_check_time(started_at)  # Lined up with the scan time
        assert scheduled_options == {
            "func": scan_then_send_health_checks,
            "trigger": "interval",
            "days": config.scheduler_minimum_days_between_scans,
            "misfire_grace_time": None,  # a late run (e.g. after the computer slept) still happens
            "id": "scan_then_send_health_checks",
            "replace_existing": True,  # a restarted lifespan does not add a duplicate job
        }

        catch_up_options = dict(catch_up.kwargs)
        run_date = catch_up_options.pop("run_date")
        assert started_at < run_date <= datetime.now(UTC) + timedelta(seconds=1)
        assert catch_up_options == {
            "func": scan_then_send_health_checks,
            "trigger": "date",
            "misfire_grace_time": None,
            "id": "catch_up_on_missed_checks",
            "replace_existing": True,
        }
        mock_start.assert_called_once()
        mock_shutdown.assert_not_called()

    mock_shutdown.assert_called_once()


@pytest.mark.anyio
async def test_schedule_scans_shuts_down_scheduler_when_app_errors(mocker: MockerFixture) -> None:
    """Tests the scheduler is still shut down if the app raises while it is running."""
    mocker.patch.object(scheduler, "add_job")
    mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")

    with pytest.raises(RuntimeError, match="app crashed"):
        async with schedule_scans(FastAPI()):
            raise RuntimeError("app crashed")

    mock_shutdown.assert_called_once()


@pytest.mark.anyio
async def test_schedule_scans_runs_job_shortly_after_startup(mocker: MockerFixture) -> None:
    """Tests a real scheduler accepts the job's options and runs it shortly after startup."""
    job_ran = asyncio.Event()

    async def fake_scan_then_send_health_checks() -> None:
        job_ran.set()

    mocker.patch("app.backend.scanning.scheduler.scheduler", AsyncIOScheduler())
    mocker.patch("app.backend.scanning.scheduler.scan_then_send_health_checks", fake_scan_then_send_health_checks)

    async with schedule_scans(FastAPI()):
        await asyncio.wait_for(job_ran.wait(), timeout=5)


def test_default_scan_interval_is_not_below_scheduler_interval() -> None:
    """Tests new websites are not given a scan interval shorter than the scheduler runs, which would scan
    them less often than they are set to."""
    assert config.scheduler_default_days_between_scans >= config.scheduler_minimum_days_between_scans


def test_next_scheduled_check_is_none_when_automatic_scans_are_off() -> None:
    """Tests there is no next check to show on the dashboard when automatic scans are not running (as in the tests,
    where `AUTOMATIC_SCANS` is off and the scheduler is never started)."""
    assert next_scheduled_check() is None


@pytest.mark.anyio
async def test_a_run_that_raises_does_not_stop_the_next_run(mocker: MockerFixture) -> None:
    """Tests a scheduled run that raises is logged by the scheduler, and the next run still happens as normal."""
    runs: list[datetime] = []
    second_run_happened = asyncio.Event()

    async def fail_the_first_run() -> None:
        runs.append(datetime.now(UTC))
        if len(runs) == 1:
            raise RuntimeError("database unavailable")
        second_run_happened.set()

    mocker.patch("app.backend.scanning.scheduler.scheduler", AsyncIOScheduler())
    mocker.patch("app.backend.scanning.scheduler.scan_then_send_health_checks", fail_the_first_run)
    mocker.patch.object(config, "scheduler_minimum_days_between_scans", 0.1 / 86_400)  # Every tenth of a second

    async with schedule_scans(FastAPI()):
        await asyncio.wait_for(second_run_happened.wait(), timeout=5)

    assert len(runs) >= 2
