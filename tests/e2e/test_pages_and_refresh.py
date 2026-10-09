"""The About page, the header and footer links between pages, the Refresh button, and cancelling Run All Scans."""

import time
from collections.abc import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, expect

from app.scanner import queued_crawls, run_all_in_progress
from tests.e2e.conftest import RunningApp

TEAM: list[str] = [
    "Ahmed Jashim",
    "Jake Spitteler",
    "Made Aria Ravindrajaya",
    "Nanasa Itami",
    "Patrick Caputi",
    "Pooja Renjith Nair",
]


def _page_html(text: str) -> str:
    return f"<html><body><h1>Example</h1><p>{text}</p></body></html>"


# ======================================
# The About page and moving between pages
# ======================================


def test_the_footer_opens_the_about_page(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests the footer's About link opens the About page, which lists the team and marks itself in the footer."""
    page = open_dashboard()

    page.locator(".site-footer").get_by_role("link", name="About").click()

    expect(page).to_have_url(app_server.url + "about")
    expect(page.get_by_role("heading", name="About Inwebstigator")).to_be_visible()
    expect(page.locator(".about-team li")).to_have_text(TEAM)
    expect(page.locator(".site-footer").get_by_role("link", name="About")).to_have_attribute("aria-current", "page")


def test_the_about_page_has_no_refresh_button(open_dashboard: Callable[..., Page]) -> None:
    """Tests the Refresh button only shows on the dashboard, while the theme toggle shows on both pages."""
    page = open_dashboard()
    expect(page.locator("#reloadPageBtn")).to_be_visible()

    page = open_dashboard("about")

    expect(page.locator("#reloadPageBtn")).to_have_count(0)
    expect(page.locator("#themeToggleBtn")).to_be_visible()


def test_every_way_back_to_the_dashboard_works(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests the app name in the header, the footer's Dashboard link and the About page's back link all go back to
    the dashboard."""
    for link in (
        lambda page: page.locator(".home-link"),
        lambda page: page.locator(".site-footer").get_by_role("link", name="Dashboard"),
        lambda page: page.locator(".back-link"),
    ):
        page = open_dashboard("about")

        link(page).click()

        expect(page).to_have_url(app_server.url)
        expect(page.get_by_role("tab", name="Websites")).to_be_visible()


def test_the_chosen_theme_carries_over_to_the_about_page(open_dashboard: Callable[..., Page]) -> None:
    """Tests a theme chosen on the dashboard is also used on the About page."""
    page = open_dashboard()
    page.emulate_media(color_scheme="light")
    page.locator("#themeToggleBtn").click()

    page.locator(".site-footer").get_by_role("link", name="About").click()

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


# ======================================
# Refresh
# ======================================


def test_refresh_shows_the_latest_results(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests Refresh reloads the dashboard, showing anything saved since it opened (e.g. by a background scan)."""
    page = open_dashboard()
    app_server.add_website("https://example.com")

    with page.expect_navigation():
        page.locator("#reloadPageBtn").click()

    expect(page.locator('.run-scan-button[data-website-url="https://example.com"]')).to_have_count(1)


def test_refresh_keeps_the_open_tab_and_cards(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests Refresh keeps whichever tab and website cards were open."""
    app_server.add_website("https://example.com")
    page = open_dashboard()
    card = page.locator(".website-card").filter(
        has=page.locator('.run-scan-button[data-website-url="https://example.com"]')
    )
    card.locator(".card-toggle").click()
    page.get_by_role("tab", name="Updates").click()

    with page.expect_navigation():
        page.locator("#reloadPageBtn").click()

    expect(page.get_by_role("tab", name="Updates")).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name="Websites").click()
    expect(card.locator(".card-toggle")).to_have_attribute("aria-expanded", "true")


# ======================================
# Cancelling Run All Scans
# ======================================


def _add_slow_websites(app_server: RunningApp) -> list[str]:
    """Adds two websites whose pages take long enough to load that Run All Scans can be cancelled part way."""
    urls = ["https://first.example.com", "https://second.example.com"]
    for url in urls:
        app_server.add_website(url)
        app_server.websites.set_page(url, _page_html("Welcome."))
    app_server.websites.delay_seconds = 1
    return urls


def _wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, f"Timed out waiting until {what}"
        time.sleep(0.05)


def _websites_requested(app_server: RunningApp) -> set[str]:
    return {urlparse(url).netloc for url in app_server.websites.requested_urls}


def test_cancelling_run_all_scans_skips_the_websites_left(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests Cancel shows only while Run All Scans runs, and cancelling stops the website being scanned, skips the
    rest, and reloads the dashboard."""
    _add_slow_websites(app_server)
    page = open_dashboard()
    cancel = page.locator("#cancel-all-scans-button")
    expect(cancel).to_be_hidden()

    with page.expect_navigation(timeout=60_000):
        page.locator("#run-all-scans-button").click()
        expect(cancel).to_be_visible()
        _wait_until(lambda: len(queued_crawls) > 0, "a website's scan started")
        cancel.click()

    expect(cancel).to_be_hidden()
    expect(page.locator("#run-all-scans-button")).to_be_enabled()
    assert not run_all_in_progress()
    assert len(_websites_requested(app_server)) == 1


def test_run_all_scans_can_be_cancelled_after_a_refresh(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests a Run All Scans still running after a refresh keeps its Cancel button, which still stops it."""
    _add_slow_websites(app_server)
    page = open_dashboard()
    page.locator("#run-all-scans-button").click()
    _wait_until(lambda: len(queued_crawls) > 0, "a website's scan started")

    with page.expect_navigation():
        page.locator("#reloadPageBtn").click()

    expect(page.locator("#run-all-scans-button")).to_be_disabled()
    expect(page.locator("#run-all-scans-message")).to_contain_text("Scanning every website")

    page.locator("#cancel-all-scans-button").click()

    expect(page.locator("#run-all-scans-message")).to_have_text(
        "Run All Scans cancelled. Press Refresh to see the latest results."
    )
    expect(page.locator("#cancel-all-scans-button")).to_be_hidden()
    _wait_until(lambda: not run_all_in_progress(), "Run All Scans stopped")
    assert len(_websites_requested(app_server)) == 1
