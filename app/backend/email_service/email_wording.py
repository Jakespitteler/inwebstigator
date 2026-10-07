import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, tzinfo
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.backend.links import website_name
from app.core.config import config
from app.models.website_models import DeactivationReason, WebsiteRead

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


class WebsiteHealth(NamedTuple):
    """How a website's monitoring is going, for the health check email.

    Attributes:
        url: The website's URL.
        status: A short sentence saying how its scans are going.
        needs_attention: Whether something is stopping it being fully monitored.
    """

    url: str
    status: str
    needs_attention: bool


def _plural(count: int, singular: str, plural: str) -> str:
    """Writes a count with the right form of a word, e.g. "1 page" or "2 pages".

    Args:
        count: How many there are.
        singular: The word for one.
        plural: The word for more than one.

    Returns:
        The count and the word.
    """
    return f"{count} {singular if count == 1 else plural}"


def _unreachable_page_count(website: WebsiteRead) -> int:
    """Counts a website's watched pages that have failed enough checks in a row to be reported.

    Args:
        website: The website.

    Returns:
        How many of its critical pages cannot be reached.
    """
    return sum(
        page.consecutive_failures >= config.critical_page_alert_after_failures for page in website.critical_pages
    )


def describe_website_health(website: WebsiteRead, now: datetime) -> WebsiteHealth:
    """Works out how a website's monitoring is going, so a health check does not say all is well when it isn't.

    Args:
        website: The website, as saved after the latest scan.
        now: The time the health check is sent, in the same form as the website's saved times.

    Returns:
        The website's status and whether it needs attention.
    """
    unreachable_pages: int = _unreachable_page_count(website)
    if not website.active:
        reason: str = (
            "it has too many pages to crawl"
            if website.deactivated_reason is DeactivationReason.TOO_LARGE
            else "its scans kept failing"
        )
        return WebsiteHealth(website.url, f"Switched off because {reason}. Its watched pages are still checked.", True)
    if website.on_cooldown_until and website.on_cooldown_until > now:
        until: str = format_email_time(website.on_cooldown_until)
        return WebsiteHealth(website.url, f"Paused until {until} after the website blocked or did not answer.", True)
    if unreachable_pages:
        pages: str = _plural(unreachable_pages, "watched page", "watched pages")
        return WebsiteHealth(website.url, f"{pages} cannot be reached.", True)
    if website.last_scan_at is None:
        return WebsiteHealth(website.url, "Not scanned yet.", False)
    return WebsiteHealth(website.url, f"Working. Last scanned {format_email_time(website.last_scan_at)}.", False)


def _website_names(website_urls: Sequence[str]) -> list[str]:
    """Names each website once, in the order given, e.g. "example.gov.au".

    Args:
        website_urls: The websites' URLs.

    Returns:
        Each website's name, without repeats.
    """
    return list(dict.fromkeys(website_name(url) for url in website_urls))


def _list_names(names: Sequence[str]) -> str:
    """Lists website names for a subject line, shortening long lists, e.g. "a.com and 3 other websites".

    Args:
        names: The website names.

    Returns:
        The names as a phrase.
    """
    if len(names) <= 2:
        return " and ".join(names)
    other_count: int = len(names) - 1
    return f"{names[0]} and {other_count} other websites"


def scan_report_subject(website_urls: Sequence[str]) -> str:
    """Writes the subject of a scan report email, naming the websites it covers.

    Args:
        website_urls: The URL of each website with a report in the email.

    Returns:
        The subject, e.g. "Website update: example.gov.au".
    """
    names: list[str] = _website_names(website_urls)
    heading: str = "Website update" if len(names) == 1 else "Website updates"
    return f"{heading}: {_list_names(names)}"


def manual_scan_subject(website_url: str) -> str:
    """Writes the subject of the email sent after a website is scanned from the dashboard.

    Args:
        website_url: The website that was scanned.

    Returns:
        The subject, e.g. "Manual scan: example.gov.au".
    """
    return f"Manual scan: {website_name(website_url)}"


def health_check_subject(website_healths: Sequence[WebsiteHealth]) -> str:
    """Writes the subject of a health check email, saying straight away if anything needs attention.

    Args:
        website_healths: How monitoring is going for each of the recipient's websites.

    Returns:
        The subject, e.g. "Health check: monitoring is running for 3 websites".
    """
    website_count: int = len(website_healths)
    websites: str = "1 website" if website_count == 1 else f"{website_count} websites"
    attention_count: int = sum(health.needs_attention for health in website_healths)
    if attention_count:
        verb: str = "needs" if attention_count == 1 else "need"
        return f"Health check: {attention_count} of {websites} {verb} attention"
    return f"Health check: monitoring is running for {websites}"
