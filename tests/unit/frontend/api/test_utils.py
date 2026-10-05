from datetime import datetime

import pytest

from app.frontend.api.utils import scan_time


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 4, 9, 5), "04 Oct 2026, 09:05"),
        (datetime(2026, 12, 31, 23, 59), "31 Dec 2026, 23:59"),
        (None, "Not scanned yet"),
    ],
)
def test_scan_time(value: datetime | None, expected: str):
    """Tests scan times are shown as a 24-hour date and time, or a note when there hasn't been a scan."""
    assert scan_time(value) == expected
