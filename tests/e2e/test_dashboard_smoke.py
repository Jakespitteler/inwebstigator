"""Smoke tests: the dashboard opens and its basic layout works, with no errors in the browser."""

from collections.abc import Callable

from playwright.sync_api import Page, expect


def test_dashboard_opens_without_errors(open_dashboard: Callable[..., Page]) -> None:
    """Tests the dashboard loads with its header and tabs, and the browser reports no errors."""
    page = open_dashboard()

    expect(page).to_have_title("Inwebstigator")
    expect(page.locator(".dashboard-header h1")).to_have_text("Inwebstigator")
    expect(page.get_by_role("tab", name="Websites")).to_be_visible()
    expect(page.get_by_role("tab", name="Updates")).to_be_visible()


def test_first_visit_opens_the_add_website_wizard(open_dashboard: Callable[..., Page]) -> None:
    """Tests that with no websites yet, the Add Website wizard is already open and there is nothing to toggle."""
    page = open_dashboard()

    expect(page.locator("#add-website-form")).to_be_visible()
    expect(page.locator("#website-url")).to_be_visible()
    expect(page.locator("#add-website-toggle")).to_have_count(0)


def test_tabs_switch_and_stay_selected_after_a_reload(open_dashboard: Callable[..., Page]) -> None:
    """Tests the Websites and Updates tabs switch panels, and the chosen tab is kept when the page reloads."""
    page = open_dashboard()
    websites_panel, updates_panel = page.locator("#websites-panel"), page.locator("#updates-panel")

    expect(websites_panel).to_be_visible()
    expect(updates_panel).to_be_hidden()

    page.get_by_role("tab", name="Updates").click()
    expect(updates_panel).to_be_visible()
    expect(websites_panel).to_be_hidden()
    expect(page).to_have_url(f"{page.url.split('#')[0]}#updates")

    page.reload()
    expect(page.get_by_role("tab", name="Updates")).to_have_attribute("aria-selected", "true")
    expect(updates_panel).to_be_visible()
