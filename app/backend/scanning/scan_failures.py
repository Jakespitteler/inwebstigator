"""How the app responds when a website's crawl fails: slowing down a website that rate limits the crawler, pausing one
that cannot be reached, and switching off one with too many pages to crawl.

Each response is saved to the website, and described in a sentence for the scan's report.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import config
from app.db.services.website_service import WebsiteService
from app.models.website_models import DeactivationReason, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)


def _plural(count: float, singular: str, plural: str) -> str:
    """Writes a count with the right form of a word, e.g. "1 hour" or "24 hours".

    Args:
        count: How many there are.
        singular: The word for one.
        plural: The word for more than one.

    Returns:
        The count and the word.
    """
    return f"{count:g} {singular if count == 1 else plural}"


def _crawl_speed(delay: float, concurrent: int) -> str:
    """Describes how gently a website is crawled, e.g. "1.5 seconds between requests, 2 at a time".

    Args:
        delay: Seconds waited before each request.
        concurrent: The most requests made at once.

    Returns:
        The description.
    """
    return f"{_plural(delay, 'second', 'seconds')} between requests, {concurrent} at a time"


def _paused_for(hours: int) -> str:
    """Says how long scheduled scans of a website are paused for after a problem.

    Args:
        hours: How long the cooldown is.

    Returns:
        A sentence saying so.
    """
    return (
        f"Scheduled scans and Run All Scans will skip it for the next {_plural(hours, 'hour', 'hours')} "
        "(Run scan still scans it straight away)."
    )


def throttle_and_cooldown(website_service: WebsiteService, id: uuid.UUID, hours: int = 24) -> WebsiteRead:
    """Increases crawler delay, decreases concurrency limits, and sets a cooldown period.

    Args:
        website_service: Reads and saves the website.
        id: The UUID identifier of the target website record.
        hours: The number of hours to keep the website on cooldown. Defaults to 24.

    Returns:
        The refreshed WebsiteRead data model instance reflecting updated throttling and cooldown settings.

    Raises:
        NotFoundError: If no website record matches the provided UUID.
    """
    website: WebsiteRead = website_service.get(id)

    new_delay: float = min(config.web_crawler_max_delay, website.recommended_delay + 0.5)
    new_concurrent: int = max(config.web_crawler_min_concurrent, website.recommended_concurrent // 2)

    on_cooldown_until = datetime.now(UTC) + timedelta(hours=hours)

    updated_website: WebsiteRead = website_service.update(
        id=id,
        model_update=WebsiteUpdate(
            recommended_delay=new_delay,
            recommended_concurrent=new_concurrent,
            on_cooldown_until=on_cooldown_until,
        ),
    )

    logger.warning(
        f"Website {website.url} throttled ({new_delay=}s, {new_concurrent=}) "
        f"and placed on cooldown until {on_cooldown_until}."
    )
    return updated_website


def handle_traffic_error(website_service: WebsiteService, website: WebsiteRead) -> str:
    """Encapsulates rate-limit policy, throttling, cool downs, and deactivation logic.

    If the crawler is operating above minimum speed limits, throttles requests and sets
    a 24-hour cooldown. If already operating at minimum crawl speed, increments the
    consecutive failure counter and sets a 24-hour cooldown, automatically deactivating
    the website (recording why) if the maximum failure threshold is reached.

    Args:
        website_service: Reads and saves the website.
        website: The website record that encountered a traffic rate-limiting error.

    Returns:
        What was done about it, for the user: how much the website's scans were slowed down, how long they are paused
        for, or that it was deactivated.
    """
    hours: int = config.website_cooldown_hours_after_throttle
    is_at_min_speed: bool = (
        website.recommended_concurrent <= config.web_crawler_min_concurrent
        and website.recommended_delay >= config.web_crawler_max_delay
    )

    if not is_at_min_speed:
        throttled: WebsiteRead = throttle_and_cooldown(website_service, id=website.id, hours=hours)
        return (
            "To go easier on the website, its scans have been slowed down to "
            f"{_crawl_speed(throttled.recommended_delay, throttled.recommended_concurrent)} "
            f"(was {_crawl_speed(website.recommended_delay, website.recommended_concurrent)}). " + _paused_for(hours)
        )

    # At minimum speed, manage consecutive failures
    website_service.set_cooldown(id=website.id, hours=hours)
    slowest_speed: str = _crawl_speed(website.recommended_delay, website.recommended_concurrent)

    if website.failed_attempts_at_min_speed >= config.web_crawler_max_failed_attempts_at_min_speed:
        website_service.update(
            id=website.id,
            model_update=WebsiteUpdate(active=False, deactivated_reason=DeactivationReason.RATE_LIMITED),
        )
        logger.warning(f"Website {website.url} deactivated as it kept rate limiting the crawler.")
        return (
            f"Its scans were already as slow as they go ({slowest_speed}), and it has now rate limited "
            f"{_plural(website.failed_attempts_at_min_speed + 1, 'scan', 'scans')} in a row at that speed, "
            "so it has been deactivated. Its critical pages are still checked for changes, but the rest of the "
            "website is no longer scanned. Turn it back on in its scan settings to try again."
        )

    website_service.update(
        id=website.id,
        model_update=WebsiteUpdate(failed_attempts_at_min_speed=website.failed_attempts_at_min_speed + 1),
    )
    scans_left: int = config.web_crawler_max_failed_attempts_at_min_speed - website.failed_attempts_at_min_speed
    return (
        f"Its scans are already as slow as they go ({slowest_speed}), so they can't be slowed down further. "
        f"{_paused_for(hours)} If it rate limits {_plural(scans_left, 'more scan', 'more scans')} in a row, "
        "it will be deactivated."
    )


def handle_too_large(website_service: WebsiteService, id: uuid.UUID, max_pages: int) -> str:
    """Deactivates a website with more pages than the crawler will scan, recording why so the
    dashboard can tell the user.

    Args:
        website_service: Reads and saves the website.
        id: Unique identifier of the website that is too large to scan.
        max_pages: The most pages the crawler would scan.

    Returns:
        Status action message explaining the website has been deactivated.
    """
    website: WebsiteRead = website_service.update(
        id=id,
        model_update=WebsiteUpdate(active=False, deactivated_reason=DeactivationReason.TOO_LARGE),
    )
    logger.warning(f"Website {website.url} deactivated as it has more than {max_pages:,} pages.")
    return (
        f"This website has more than {max_pages:,} pages, which is more than the crawler will scan, "
        "so it has been deactivated. Its critical pages are still checked for changes, "
        "but the rest of the website is no longer scanned."
    )


def handle_connection_error(website_service: WebsiteService, website_id: uuid.UUID) -> str:
    """Handles unreachable site errors by setting a standard 2-hour cooldown period.

    Args:
        website_service: Reads and saves the website.
        website_id: Unique identifier of the unreachable website.

    Returns:
        What was done about it, for the user: how long its scheduled scans are paused for.
    """
    website_service.set_cooldown(id=website_id, hours=config.website_cooldown_hours_after_unreachable)
    return _paused_for(config.website_cooldown_hours_after_unreachable)
