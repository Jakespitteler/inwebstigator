from datetime import datetime, timedelta

from app.backend.email_service.website_health import describe_website_health
from app.models.website_models import DeactivationReason
from tests.unit.backend.email_service.builders import make_page, make_website

NOW = datetime(2026, 10, 7, 9, 0)


def test_a_website_scanned_without_problems_is_working() -> None:
    health = describe_website_health(make_website(last_scan_at=NOW - timedelta(hours=3)), NOW)

    assert health.needs_attention is False
    assert health.status.startswith("Working. Last scanned 07 Oct 2026, 06:00")


def test_a_website_not_scanned_yet_does_not_need_attention() -> None:
    health = describe_website_health(make_website(), NOW)

    assert (health.status, health.needs_attention) == ("Not scanned yet.", False)


def test_a_switched_off_website_needs_attention() -> None:
    website = make_website(active=False, deactivated_reason=DeactivationReason.TOO_LARGE)

    health = describe_website_health(website, NOW)

    assert health.needs_attention is True
    assert "too many pages to crawl" in health.status


def test_a_website_on_cooldown_needs_attention() -> None:
    health = describe_website_health(make_website(on_cooldown_until=NOW + timedelta(hours=2)), NOW)

    assert health.needs_attention is True
    assert health.status.startswith("Paused until 07 Oct 2026, 11:00")


def test_a_website_with_unreachable_watched_pages_needs_attention() -> None:
    pages = [make_page(consecutive_failures=2), make_page("https://example.gov.au/ok")]

    health = describe_website_health(make_website(critical_pages=pages, last_scan_at=NOW), NOW)

    assert (health.status, health.needs_attention) == ("1 watched page cannot be reached.", True)
