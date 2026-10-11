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
        Status action message summarising the mitigation applied (throttled, cooldown, or deactivated).
    """

    is_at_min_speed: bool = (
        website.recommended_concurrent <= config.web_crawler_min_concurrent
        and website.recommended_delay >= config.web_crawler_max_delay
    )

    if not is_at_min_speed:
        throttle_and_cooldown(website_service, id=website.id, hours=config.website_cooldown_hours_after_throttle)
        return "Website throttled and placed on cooldown."

    # At minimum speed, manage consecutive failures
    website_service.set_cooldown(id=website.id, hours=config.website_cooldown_hours_after_throttle)

    if website.failed_attempts_at_min_speed >= config.web_crawler_max_failed_attempts_at_min_speed:
        website_service.update(
            id=website.id,
            model_update=WebsiteUpdate(active=False, deactivated_reason=DeactivationReason.RATE_LIMITED),
        )
        return f"Failed {website.failed_attempts_at_min_speed} times. Deactivating: {website.url}"

    website_service.update(
        id=website.id,
        model_update=WebsiteUpdate(failed_attempts_at_min_speed=website.failed_attempts_at_min_speed + 1),
    )
    return "Website placed on cooldown."


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
        Status action message confirming cooldown placement.
    """
    website_service.set_cooldown(id=website_id, hours=config.website_cooldown_hours_after_unreachable)
    return "Website placed on cooldown."
