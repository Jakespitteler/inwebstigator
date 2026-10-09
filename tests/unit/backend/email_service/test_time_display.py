from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from app.backend.email_service.email_wording import format_email_time
from app.core.config import config


def test_times_are_shown_in_the_configured_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "email_time_zone", "Australia/Perth")

    assert format_email_time(datetime(2026, 10, 7, 2, 56, tzinfo=UTC)) == "07 Oct 2026, 10:56 AWST"


def test_an_unknown_time_zone_falls_back_to_the_computers_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "email_time_zone", "Not/AZone")
    moment = datetime(2026, 10, 7, 2, 56, tzinfo=UTC)

    assert format_email_time(moment).startswith(f"{moment.astimezone():%d %b %Y, %H:%M}")


def test_a_time_without_a_time_zone_is_read_as_the_computers_own_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "email_time_zone", "")

    assert format_email_time(datetime(2026, 10, 7, 9, 30)).startswith("07 Oct 2026, 09:30")


class LongNamedZone(tzinfo):
    """A time zone that, like Windows' time zones, only has a long name."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=8)

    def tzname(self, dt: datetime | None) -> str:
        return "W. Australia Standard Time"

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


def test_a_time_zone_with_only_a_long_name_is_shown_as_an_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests Windows' long names such as "W. Australia Standard Time" are shortened to "UTC+08:00"."""
    monkeypatch.setattr("app.backend.email_service.email_wording._email_time_zone", LongNamedZone)

    assert format_email_time(datetime(2026, 10, 7, 1, 30, tzinfo=UTC)) == "07 Oct 2026, 09:30 UTC+08:00"


def test_a_blank_time_zone_shows_times_in_the_computers_own_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests a blank EMAIL_TIME_ZONE shows the app's UTC times in the computer's own time zone."""
    monkeypatch.setattr(config, "email_time_zone", "")
    moment = datetime(2026, 10, 7, 2, 56, tzinfo=UTC)
    local_moment: datetime = moment.astimezone()

    assert format_email_time(moment).startswith(f"{local_moment:%d %b %Y, %H:%M} ")


@pytest.mark.parametrize(
    ("time_zone", "expected"),
    [
        ("America/Sao_Paulo", "06 Oct 2026, 23:56 UTC-03:00"),
        ("Asia/Kolkata", "07 Oct 2026, 08:26 IST"),
    ],
)
def test_a_time_zone_named_only_by_its_offset_is_shown_as_a_utc_offset(
    monkeypatch: pytest.MonkeyPatch, time_zone: str, expected: str
) -> None:
    """Tests a time zone whose short name is only a number (e.g. "-03") is shown as "UTC-03:00", behind UTC as well as
    ahead, while a real short name such as "IST" is kept."""
    monkeypatch.setattr(config, "email_time_zone", time_zone)

    assert format_email_time(datetime(2026, 10, 7, 2, 56, tzinfo=UTC)) == expected


def test_a_time_zone_written_like_a_file_path_falls_back_to_the_computers_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests a time zone that is not a valid name at all (e.g. "../Perth") does not stop emails being written."""
    monkeypatch.setattr(config, "email_time_zone", "../Perth")
    moment = datetime(2026, 10, 7, 2, 56, tzinfo=UTC)

    assert format_email_time(moment).startswith(f"{moment.astimezone():%d %b %Y, %H:%M}")
