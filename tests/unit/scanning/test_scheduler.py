import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI
from pytest_mock import MockerFixture

from app.core.config import config
from app.scanning.scheduler import (
    SCAN_JOB_ID,
    restart_scan_countdown,
    scan_then_send_health_checks,
    schedule_scans,
    scheduler,
)


@pytest.mark.anyio
async def test_scan_then_send_health_checks_sends_health_checks_after_scanning(mocker: MockerFixture) -> None:
    """Tests health checks go out after the scan, so its change emails count as recent contact and a
    recipient is not told nothing has changed just after being told what did."""
    calls: list[str] = []

    def record(step: str) -> Callable[..., None]:
        return lambda *args, **kwargs: calls.append(step)

    mocker.patch("app.scanning.scheduler.scan_all_websites", side_effect=record("scan"))
    mocker.patch("app.scanning.scheduler.send_due_health_checks", side_effect=record("health_checks"))

    await scan_then_send_health_checks()

    assert calls == ["scan", "health_checks"]


@pytest.mark.anyio
async def test_scan_then_send_health_checks_sends_no_health_checks_when_the_scan_fails(mocker: MockerFixture) -> None:
    """Tests recipients are not told nothing has changed when the scan itself failed."""
    mocker.patch("app.scanning.scheduler.scan_all_websites", side_effect=RuntimeError("database unavailable"))
    mock_send_due_health_checks = mocker.patch("app.scanning.scheduler.send_due_health_checks")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await scan_then_send_health_checks()

    mock_send_due_health_checks.assert_not_called()


def test_restart_scan_countdown_gives_the_scan_job_a_new_interval_from_now(mocker: MockerFixture) -> None:
    """Tests the scan job gets a fresh interval trigger, which next fires one full interval from now."""
    mocker.patch.object(scheduler, "get_job", return_value=mocker.Mock())
    mock_reschedule_job = mocker.patch.object(scheduler, "reschedule_job")

    restart_scan_countdown()

    mock_reschedule_job.assert_called_once_with(
        job_id=SCAN_JOB_ID, trigger="interval", days=config.scheduler_minimum_days_between_scans
    )


def test_restart_scan_countdown_does_nothing_when_automatic_scans_are_off(mocker: MockerFixture) -> None:
    """Tests nothing is rescheduled when there is no scan job, as automatic scans are not running."""
    mocker.patch.object(scheduler, "get_job", return_value=None)
    mock_reschedule_job = mocker.patch.object(scheduler, "reschedule_job")

    restart_scan_countdown()

    mock_reschedule_job.assert_not_called()


@pytest.mark.anyio
async def test_schedule_scans_lifespan(mocker: MockerFixture) -> None:
    """Tests lifespan initialisation: one job covering scans and health checks, first run shortly after
    startup to catch up on anything missed while the app was closed, and the scheduler's lifecycle."""
    mock_add_job = mocker.patch.object(scheduler, "add_job")
    mock_start = mocker.patch.object(scheduler, "start")
    mock_shutdown = mocker.patch.object(scheduler, "shutdown")
    started_at = datetime.now()

    async with schedule_scans(FastAPI()):
        mock_add_job.assert_called_once()
        assert mock_add_job.call_args.args == ()
        job_options = dict(mock_add_job.call_args.kwargs)
        next_run_time = job_options.pop("next_run_time")
        assert started_at < next_run_time <= datetime.now() + timedelta(seconds=1)
        assert job_options == {
            "func": scan_then_send_health_checks,
            "trigger": "interval",
            "days": config.scheduler_minimum_days_between_scans,
            "misfire_grace_time": None,  # a late run (e.g. after the computer slept) still happens
            "id": "scan_then_send_health_checks",
            "replace_existing": True,  # a restarted lifespan does not add a duplicate job
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

    mocker.patch("app.scanning.scheduler.scheduler", AsyncIOScheduler())
    mocker.patch("app.scanning.scheduler.scan_then_send_health_checks", fake_scan_then_send_health_checks)

    async with schedule_scans(FastAPI()):
        await asyncio.wait_for(job_ran.wait(), timeout=5)


def test_default_scan_interval_is_not_below_scheduler_interval() -> None:
    """Tests new websites are not given a scan interval shorter than the scheduler runs, which would scan
    them less often than they are set to."""
    assert config.scheduler_default_days_between_scans >= config.scheduler_minimum_days_between_scans
