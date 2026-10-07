from datetime import datetime
from typing import NamedTuple

from app.backend.email_service.time_display import format_email_time
from app.core.config import config
from app.models.website_models import DeactivationReason, WebsiteRead


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
