from datetime import datetime, timedelta

from app.backend.email_service.email_wording import describe_website_health
from app.models.website_models import DeactivationReason
from tests.unit.backend.email_service.builders import make_page, make_website

NOW = datetime(2026, 10, 7, 9, 0).astimezone()


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


def test_a_website_switched_off_because_its_scans_kept_failing_needs_attention() -> None:
    """Tests a website switched off for failing too often (no other reason saved) says so and needs attention."""
    health = describe_website_health(make_website(active=False, deactivated_reason=None), NOW)

    assert health.needs_attention is True
    assert health.status == "Switched off because its scans kept failing. Its watched pages are still checked."


def test_a_website_whose_cooldown_has_ended_is_working_again() -> None:
    """Tests a cooldown that ended before the health check is not reported as a pause."""
    website = make_website(on_cooldown_until=NOW - timedelta(minutes=1), last_scan_at=NOW - timedelta(hours=1))

    health = describe_website_health(website, NOW)

    assert health.needs_attention is False
    assert health.status.startswith("Working.")


def test_only_watched_pages_that_failed_enough_checks_in_a_row_are_reported() -> None:
    """Tests a page that failed once (below CRITICAL_PAGE_ALERT_AFTER_FAILURES) is not counted, and two unreachable
    pages are counted with the plural."""
    pages = [
        make_page("https://example.gov.au/a", consecutive_failures=2),
        make_page("https://example.gov.au/b", consecutive_failures=3),
        make_page("https://example.gov.au/c", consecutive_failures=1),
    ]

    health = describe_website_health(make_website(critical_pages=pages, last_scan_at=NOW), NOW)

    assert (health.status, health.needs_attention) == ("2 watched pages cannot be reached.", True)
