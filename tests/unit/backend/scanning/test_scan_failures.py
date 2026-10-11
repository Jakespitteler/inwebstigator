from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.backend.scanning.scan_failures import (
    handle_connection_error,
    handle_too_large,
    handle_traffic_error,
    throttle_and_cooldown,
)
from app.core.config import config
from app.db.services.website_service import WebsiteService
from app.models.website_models import DeactivationReason, WebsiteRead, WebsiteUpdate


def test_throttle_and_cooldown(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests throttling parameters (increasing delay, decreasing concurrency)
    and placing a website on cooldown.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    initial_delay = test_website.recommended_delay
    initial_concurrent = test_website.recommended_concurrent

    hours = 24
    updated_website: WebsiteRead = throttle_and_cooldown(WebsiteService(session), id=test_website.id, hours=hours)

    assert updated_website.on_cooldown_until is not None
    assert updated_website.recommended_delay > initial_delay
    assert updated_website.recommended_concurrent < initial_concurrent
    assert updated_website.on_cooldown_until > datetime.now(UTC) + timedelta(hours=hours - 1)


def test_throttle_and_cooldown_clamped_to_config_limits(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests that throttling respects maximum delay and minimum concurrency limits defined in config.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Force site attributes to maximum/minimum bounds before throttling
    service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
        ),
    )

    updated_website = throttle_and_cooldown(service, id=test_website.id)

    assert updated_website.recommended_delay == config.web_crawler_max_delay
    assert updated_website.recommended_concurrent == config.web_crawler_min_concurrent


def test_handle_traffic_error_throttles_when_not_at_min_speed(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error when website crawler parameters have not reached minimum speed limits.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Configure website to be above minimum speed limits
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay - 0.1,
            recommended_concurrent=config.web_crawler_min_concurrent + 1,
        ),
    )

    result_message = handle_traffic_error(service, website=website)

    assert result_message == "Website throttled and placed on cooldown."

    fetched_website = service.get(id=website.id)
    assert fetched_website.recommended_delay > website.recommended_delay
    assert fetched_website.recommended_concurrent < website.recommended_concurrent
    assert fetched_website.on_cooldown_until is not None
    assert fetched_website.on_cooldown_until > datetime.now(UTC)


def test_handle_traffic_error_increments_attempts_at_min_speed(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error increments failed attempts when already operating at minimum speed.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Configure website to be at minimum speed limits
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
            failed_attempts_at_min_speed=0,
        ),
    )

    result_message = handle_traffic_error(service, website=website)

    assert result_message == "Website placed on cooldown."

    fetched_website = service.get(id=website.id)
    assert fetched_website.failed_attempts_at_min_speed == 1
    assert fetched_website.on_cooldown_until is not None


def test_handle_traffic_error_deactivates_website_at_max_failures(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error deactivates the website upon reaching maximum failed attempts at minimum speed.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)
    max_failures = config.web_crawler_max_failed_attempts_at_min_speed

    # Set website to max failure threshold
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
            failed_attempts_at_min_speed=max_failures,
            active=True,
        ),
    )

    result_message = handle_traffic_error(service, website=website)

    assert f"Failed {max_failures} times. Deactivating: {website.url}" in result_message

    fetched_website = service.get(id=website.id)
    assert fetched_website.active is False
    assert fetched_website.deactivated_reason == DeactivationReason.RATE_LIMITED


def test_reactivating_a_rate_limited_website_starts_its_failed_attempts_again(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests switching a website back on after it was deactivated for rate limiting clears its failed attempts, so
    a single rate limit does not switch it off again straight away."""
    service = WebsiteService(session)
    service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
            failed_attempts_at_min_speed=config.web_crawler_max_failed_attempts_at_min_speed,
        ),
    )
    handle_traffic_error(service, website=service.get(test_website.id))

    reactivated = service.update(id=test_website.id, model_update=WebsiteUpdate(active=True))
    handle_traffic_error(service, website=reactivated)

    fetched_website = service.get(id=test_website.id)
    assert (fetched_website.active, fetched_website.failed_attempts_at_min_speed) == (True, 1)


def test_handle_connection_error(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_connection_error sets a 2-hour cooldown period and returns proper status message.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    result_message = handle_connection_error(service, website_id=test_website.id)

    assert result_message == "Website placed on cooldown."

    fetched_website = service.get(id=test_website.id)
    assert fetched_website.on_cooldown_until is not None
    assert fetched_website.on_cooldown_until > datetime.now(UTC) + timedelta(hours=1, minutes=59)
    assert fetched_website.on_cooldown_until <= datetime.now(UTC) + timedelta(hours=2)


def test_handle_too_large_deactivates_website_and_records_why(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_too_large deactivates the website, records it was too large, and explains the page limit.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    result_message = handle_too_large(service, id=test_website.id, max_pages=50_000)

    assert "more than 50,000 pages" in result_message

    fetched_website = service.get(id=test_website.id)
    assert fetched_website.active is False
    assert fetched_website.deactivated_reason == DeactivationReason.TOO_LARGE
