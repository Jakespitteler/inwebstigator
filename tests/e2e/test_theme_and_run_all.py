"""The light/dark theme toggle, and the "Run All Scans" button."""

from collections.abc import Callable
from datetime import datetime, timedelta

from playwright.sync_api import Page, expect

from tests.e2e.conftest import RunningApp

# ======================================
# Light and dark theme
# ======================================


def _theme(page: Page) -> str | None:
    return page.evaluate("document.documentElement.dataset.theme")


def _toggle_label(page: Page) -> str:
    """The toggle's label, which is drawn by the stylesheet so it is right from the first paint."""
    return page.evaluate(
        "getComputedStyle(document.getElementById('themeToggleLabel'), '::after').content.replaceAll('\"', '')"
    )


def _saved_theme(page: Page) -> str | None:
    return page.evaluate("localStorage.getItem('inwebstigator-theme')")


def test_the_toggle_switches_between_light_and_dark(open_dashboard: Callable[..., Page]) -> None:
    """Tests the toggle switches the theme, its label always offers the other theme, and the choice is saved."""
    page = open_dashboard()
    page.emulate_media(color_scheme="light")
    page.reload()
    toggle = page.locator("#themeToggleBtn")

    assert (_theme(page), _toggle_label(page)) == ("light", "Dark mode")

    toggle.click()
    assert (_theme(page), _toggle_label(page), _saved_theme(page)) == ("dark", "Light mode", "dark")

    toggle.click()
    assert (_theme(page), _toggle_label(page), _saved_theme(page)) == ("light", "Dark mode", "light")


def test_the_chosen_theme_is_kept_after_a_reload(open_dashboard: Callable[..., Page]) -> None:
    """Tests a chosen theme is still used after the dashboard reloads, even if the computer's setting differs."""
    page = open_dashboard()
    page.emulate_media(color_scheme="light")
    page.locator("#themeToggleBtn").click()

    page.reload()

    assert _theme(page) == "dark"


def test_a_first_visit_follows_the_computers_setting(open_dashboard: Callable[..., Page]) -> None:
    """Tests that before a theme is chosen, the dashboard uses the computer's light or dark setting."""
    page = open_dashboard()
    page.emulate_media(color_scheme="dark")
    page.reload()

    assert (_theme(page), _toggle_label(page), _saved_theme(page)) == ("dark", "Light mode", None)


def test_the_computers_setting_is_followed_until_a_theme_is_chosen(open_dashboard: Callable[..., Page]) -> None:
    """Tests the dashboard follows the computer switching between light and dark, until a theme is chosen on the
    dashboard, which then stays."""
    page = open_dashboard()
    page.emulate_media(color_scheme="light")
    page.reload()

    page.emulate_media(color_scheme="dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    page.locator("#themeToggleBtn").click()  # Choose light
    page.emulate_media(color_scheme="light")
    page.emulate_media(color_scheme="dark")
    assert _theme(page) == "light"


def test_the_saved_theme_is_applied_before_the_page_appears(open_dashboard: Callable[..., Page]) -> None:
    """Tests a saved dark theme is already set when the page's body first appears, before anything can be shown,
    so the dashboard never flashes light, and the transitions held back while loading are turned back on."""
    page = open_dashboard()
    page.emulate_media(color_scheme="light")
    page.evaluate("localStorage.setItem('inwebstigator-theme', 'dark')")
    page.add_init_script(
        "new MutationObserver((changes, observer) => {"
        "  if (document.body) {"
        "    window.themeWhenBodyAppeared = document.documentElement.dataset.theme;"
        "    observer.disconnect();"
        "  }"
        "}).observe(document, { childList: true, subtree: true });"
    )

    page.reload()

    assert page.evaluate("window.themeWhenBodyAppeared") == "dark"
    expect(page.locator("html")).not_to_have_class("preloading")


# ======================================
# Run All Scans
# ======================================


def _page_html(text: str) -> str:
    return f"<html><body><h1>Example</h1><p>{text}</p></body></html>"


def _add_website(page: Page, app_server: RunningApp, url: str, text: str, recipients: list[str]) -> None:
    """Adds a website through the app, so its first scan saves the page as it is now."""
    app_server.websites.set_page(url, _page_html(text))
    response = page.request.post(
        f"{app_server.url}scanner/initial_scan", data={"url": url, "recipient_emails": recipients}
    )
    assert response.ok, response.text()


def test_run_all_scans_scans_every_website_and_sends_each_recipient_one_report(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests "Run All Scans" scans every website, even ones just scanned, shows its progress, then reloads with what
    changed, and each recipient gets one report covering every website."""
    page = open_dashboard()
    _add_website(page, app_server, "https://first.example.com", "The fee is $100.", ["team@example.com"])
    _add_website(page, app_server, "https://second.example.com", "Applications close in May.", ["team@example.com"])
    app_server.sent_emails.reset()
    page.reload()

    app_server.websites.set_page("https://first.example.com", _page_html("The fee is $120."))
    app_server.websites.set_page("https://second.example.com", _page_html("Applications close in June."))
    app_server.websites.delay_seconds = 0.2  # Long enough to see the progress message
    button = page.locator("#run-all-scans-button")

    with page.expect_navigation(timeout=60_000):
        button.click()
        expect(button).to_be_disabled()
        expect(page.locator("#run-all-scans-message")).to_contain_text("Scanning every website")

    page.get_by_role("tab", name="Updates").click()
    expect(page.locator(".website-change-record .website-name")).to_have_count(2)
    expect(page.locator("#updates-panel")).to_contain_text("$120")
    expect(page.locator("#updates-panel")).to_contain_text("June")
    assert [(email.to, email.subject) for email in app_server.sent_emails.emails] == [
        ("team@example.com", "Website Update")
    ]


def test_run_all_scans_skips_websites_on_cooldown(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests "Run All Scans" leaves a website on cooldown (e.g. after being rate limited) alone."""
    app_server.add_website("https://resting.example.com", on_cooldown_until=datetime.now() + timedelta(hours=2))
    app_server.add_website("https://ready.example.com")
    for url in ("https://resting.example.com", "https://ready.example.com"):
        app_server.websites.set_page(url, _page_html("Welcome."))
    page = open_dashboard()

    with page.expect_navigation(timeout=60_000):
        page.locator("#run-all-scans-button").click()

    requested = app_server.websites.requested_urls
    assert any(url.startswith("https://ready.example.com") for url in requested)
    assert not any(url.startswith("https://resting.example.com") for url in requested)
