import logging
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import config

logger: logging.Logger = logging.getLogger(__name__)


def _email_time_zone() -> tzinfo | None:
    """Gets the time zone emails show times in.

    Returns:
        The configured time zone (e.g. "Australia/Perth"), or None to use the computer's own time zone, including
        when the configured one is not a real time zone.
    """
    if not config.email_time_zone:
        return None
    try:
        return ZoneInfo(config.email_time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("%r is not a time zone, so emails use this computer's time zone.", config.email_time_zone)
        return None


def _utc_offset_label(offset: timedelta) -> str:
    """Writes a time zone's offset from UTC, e.g. "UTC+08:00".

    Args:
        offset: How far ahead of UTC (or behind, if negative) the time zone is.

    Returns:
        The offset as a label.
    """
    sign: str = "-" if offset < timedelta(0) else "+"
    hours, minutes = divmod(abs(offset).seconds // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _time_zone_label(moment: datetime) -> str:
    """Names the time zone of a time, e.g. "AWST", or "UTC+08:00" where only a long name is available.

    Windows gives long names such as "W. Australia Standard Time", which are too long for an email.

    Args:
        moment: A time with a time zone.

    Returns:
        A short label for its time zone.
    """
    name: str | None = moment.tzname()
    if name and " " not in name and not name.startswith(("+", "-")):
        return name
    return _utc_offset_label(moment.utcoffset() or timedelta(0))


def format_email_time(moment: datetime) -> str:
    """Shows a time in an email, in the configured time zone, e.g. "07 Oct 2026, 10:56 AWST".

    The app saves most times without a time zone, in the computer's own time, so those are read as local time.

    Args:
        moment: The time to show.

    Returns:
        The date, time and time zone.
    """
    local_moment: datetime = moment.astimezone(_email_time_zone())
    return f"{local_moment:%d %b %Y, %H:%M} {_time_zone_label(local_moment)}"
