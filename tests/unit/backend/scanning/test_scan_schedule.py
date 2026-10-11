from datetime import UTC, datetime, time, timedelta

import pytest
from pytest_mock import MockerFixture

from app.backend.scanning.scan_schedule import is_due_a_scan, latest_check_time
from app.core.config import config
from app.models.website_models import WebsiteRead


@pytest.fixture
def checks_at_8am_and_8pm(mocker: MockerFixture) -> None:
    """Makes the scheduled checks 8am and 8pm on the computer's clock, as they are by default."""
    mocker.patch.object(config, "scheduler_scan_time", time(8, 0))
    mocker.patch.object(config, "scheduler_minimum_days_between_scans", 0.5)


def _local(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Returns a time in January 2026 on the computer's own clock, as the scheduled checks are."""
    return datetime(2026, 1, day, hour, minute, second).astimezone()


@pytest.mark.usefixtures("checks_at_8am_and_8pm")
@pytest.mark.parametrize(
    ("moment", "check"),
    [
        (_local(1, 8), _local(1, 8)),
        (_local(1, 15, 30), _local(1, 8)),
        (_local(1, 20), _local(1, 20)),
        (_local(1, 23, 59), _local(1, 20)),
        (_local(2, 7, 59), _local(1, 20)),
    ],
    ids=["at-the-morning-check", "afternoon", "at-the-evening-check", "late-at-night", "just-before-the-morning-check"],
)
def test_latest_check_time_is_the_check_a_moment_falls_in(moment: datetime, check: datetime) -> None:
    """Tests a moment falls in the latest scheduled check at or before it, e.g. 3:30pm in the 8am check."""
    assert latest_check_time(moment) == check


@pytest.mark.usefixtures("checks_at_8am_and_8pm")
@pytest.mark.parametrize(
    ("days_between_scans", "last_scan_at", "run_started_at", "expected_due"),
    [
        (1, None, _local(1, 8), True),
        (1, _local(1, 8), _local(1, 20), False),
        (1, _local(1, 8), _local(2, 8), True),
        (1, _local(1, 17), _local(2, 8), True),
        (1, _local(1, 8, 0, 5), _local(2, 8, 0, 2), True),
        (1.5, _local(1, 20), _local(2, 20), False),
        (1.5, _local(1, 20), _local(3, 8), True),
        (0.5, _local(1, 8), _local(1, 20), True),
        (0.5, _local(1, 20, 5), _local(1, 20, 30), False),
    ],
    ids=[
        "never-scanned",
        "a-day-not-yet-passed",
        "a-day-passed",
        "scanned-late-after-the-morning-check-was-missed",
        "run-started-a-few-seconds-earlier-than-last-time",
        "a-day-and-a-half-not-yet-passed",
        "a-day-and-a-half-passed",
        "half-a-day-passed",
        "same-check",
    ],
)
def test_is_due_a_scan_counts_between_the_checks_the_scans_fall_in(
    populated_website: WebsiteRead,
    days_between_scans: float,
    last_scan_at: datetime | None,
    run_started_at: datetime,
    expected_due: bool,
):
    """Tests a website is due a scan once its interval has passed between the scheduled checks its last scan and
    this run fall in, so a scan that ran late (e.g. at 5pm after the app was opened) is still followed by one at the
    next morning's check, and intervals such as 1.5 days are not rounded down to whole days."""
    website = populated_website.model_copy(
        update={"days_between_scans": days_between_scans, "last_scan_at": last_scan_at}
    )

    assert is_due_a_scan(website, run_started_at) is expected_due


def test_is_due_a_scan_tolerance_is_at_most_half_the_time_between_runs(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests that when runs are minutes apart, a website set to scan every half hour is scanned at the run nearest
    to when it is due, rather than at every run because of the usual hour's tolerance."""
    mocker.patch.object(config, "scheduler_minimum_days_between_scans", 0.0034)  # Runs every ~4.9 minutes
    last_scan_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    website = populated_website.model_copy(update={"days_between_scans": 0.02, "last_scan_at": last_scan_at})
    runs = [last_scan_at + timedelta(days=0.0034) * run for run in range(1, 8)]

    due_runs = [run for run in runs if is_due_a_scan(website, run)]

    assert due_runs[0] == runs[5]  # The 6th run, 29.4 minutes after the last scan, is the nearest to 28.8 minutes


@pytest.mark.usefixtures("checks_at_8am_and_8pm")
def test_an_interval_a_little_longer_than_a_day_is_scanned_at_the_nearest_check(populated_website: WebsiteRead):
    """Tests an interval up to `SCHEDULER_SCAN_DUE_TOLERANCE_MINUTES` longer than a day is still scanned at the next
    morning's check, rather than waiting until the evening, while a longer one waits."""
    tolerance = timedelta(minutes=config.scheduler_scan_due_tolerance_minutes)
    just_within = populated_website.model_copy(
        update={"days_between_scans": (timedelta(days=1) + tolerance) / timedelta(days=1), "last_scan_at": _local(1, 8)}
    )
    just_over = just_within.model_copy(
        update={"days_between_scans": (timedelta(days=1) + tolerance + timedelta(minutes=1)) / timedelta(days=1)}
    )

    assert is_due_a_scan(just_within, _local(2, 8))
    assert not is_due_a_scan(just_over, _local(2, 8))
    assert is_due_a_scan(just_over, _local(2, 20))
