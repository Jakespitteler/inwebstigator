from datetime import UTC, datetime

import pytest

from app.frontend.api.utils import format_timestamp, scan_time


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 4, 9, 5).astimezone(), "04 Oct 2026, 09:05"),
        (datetime(2026, 12, 31, 23, 59).astimezone(), "31 Dec 2026, 23:59"),
        (None, "Not scanned yet"),
    ],
)
def test_scan_time(value: datetime | None, expected: str):
    """Tests scan times are shown as a 24-hour date and time, or a note when there hasn't been a scan."""
    assert scan_time(value) == expected


def test_utc_times_are_shown_in_the_computers_own_time_zone() -> None:
    """Tests a time saved in UTC is shown on the dashboard in the user's own time, not in UTC."""
    saved_time = datetime(2026, 10, 4, 1, 5, tzinfo=UTC)

    assert format_timestamp(saved_time) == f"{saved_time.astimezone():%d %b %Y, %H:%M}"
