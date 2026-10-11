"""Website cards: opening them, scanning, and managing a website's critical pages, emails, settings and deletion."""

import re
import time
from collections.abc import Callable

from playwright.sync_api import Locator, Page, expect

from app.backend.scanning.scan_failures import handle_too_large
from app.backend.scanning.scan_queue import scan_queue
from app.db.services.website_service import WebsiteService
from tests.e2e.conftest import API_HEADERS, RunningApp

# As the app saves it, with the "/" a website's address always gets
WEBSITE_URL: str = "https://example.com/"
FEES_URL: str = "https://example.com/fees"


def _page_html(text: str) -> str:
    return f"<html><body><h1>Example</h1><p>{text}</p></body></html>"


def _card(page: Page, url: str = WEBSITE_URL) -> Locator:
    """Finds a website's card by its address, which stays the same however its display name is worked out."""
    return page.locator(".website-card").filter(has=page.locator(f'.run-scan-button[data-website-url="{url}"]'))


def _open_card(page: Page, url: str = WEBSITE_URL) -> Locator:
    """Opens a website's card, showing everything below its header."""
    card = _card(page, url)
    card.locator(".card-toggle").click()
    expect(card.locator(".card-body")).to_be_visible()
    return card


def _wait_for_scan_to_start(url: str) -> None:
    deadline = time.monotonic() + 10
    while url not in scan_queue.queued_urls:
        assert time.monotonic() < deadline, f"The scan of {url} never started"
        time.sleep(0.05)


# ======================================
# Opening and closing a card
# ======================================


def test_a_card_opens_and_closes_and_stays_open_after_a_reload(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests a card starts closed, opens when clicked, closes from its header, and is remembered as open."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard()
    card = _card(page)
    details, toggle = card.locator(".card-body"), card.locator(".card-toggle")

    expect(details).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")

    card.locator(".website-name").click()
    expect(details).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")

    page.reload()
    expect(_card(page).locator(".card-body")).to_be_visible()

    _card(page).locator(".website-name").click()
    expect(_card(page).locator(".card-body")).to_be_hidden()


def test_a_card_shows_the_main_page_as_automatic(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests the website's own address is listed as a critical page that can't be deleted, and others can be."""
    app_server.add_website(WEBSITE_URL, critical_pages=[FEES_URL])
    card = _open_card(open_dashboard())

    pages = card.locator(".critical-page-list li")
    expect(pages).to_have_count(2)
    expect(pages.filter(has_text="Main website (automatic)")).to_have_count(1)
    expect(card.locator(".delete-critical-page-button")).to_have_count(1)


# ======================================
# Scanning
# ======================================


def test_run_scan_finds_a_change_and_refreshes_the_dashboard(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests "Run scan" scans the website, then the dashboard reloads showing what changed."""
    app_server.websites.set_page(WEBSITE_URL, _page_html("The fee is $100."))
    page = open_dashboard()
    response = page.request.post(
        f"{app_server.url}scanner/initial_scan", data={"url": WEBSITE_URL}, headers=API_HEADERS
    )
    assert response.ok, response.text()
    page.reload()

    app_server.websites.set_page(WEBSITE_URL, _page_html("The fee is $120."))
    with page.expect_navigation():
        _card(page).locator(".run-scan-button").click()

    expect(page.locator("#updates-panel")).to_contain_text("$120")
    [website] = app_server.saved_websites()
    assert "$120" in website.critical_pages[0].text_body


def test_cancelling_a_scan_from_its_card(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests a running scan can be cancelled from the website's card, which then lets it be run again."""
    app_server.add_website(WEBSITE_URL)
    app_server.websites.set_page(WEBSITE_URL, _page_html("Welcome."))
    page = open_dashboard()
    card = _card(page)
    run_button, cancel_button = card.locator(".run-scan-button"), card.locator(".cancel-scan-button")

    app_server.websites.delay_seconds = 0.5  # Slow the scan down enough to cancel it
    run_button.click()
    expect(run_button).to_be_disabled()
    expect(card.locator(".run-scan-message")).to_contain_text("Scan queued")

    _wait_for_scan_to_start(WEBSITE_URL)
    cancel_button.click()

    expect(card.locator(".run-scan-message")).to_contain_text(re.compile("cancelled", re.IGNORECASE))
    expect(cancel_button).to_be_hidden()
    expect(run_button).to_be_enabled()


# ======================================
# Critical pages
# ======================================


def test_adding_a_critical_page_scans_and_lists_it(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests a critical page added to a website is loaded, saved with its first copy, and listed on the card."""
    app_server.add_website(WEBSITE_URL)
    app_server.websites.set_page(FEES_URL, _page_html("The fee is $100."))
    card = _open_card(open_dashboard())

    card.locator(".new-critical-page-url").fill(FEES_URL)
    card.get_by_role("button", name="Add critical page").click()

    expect(_card(card.page).locator(".critical-page-list")).to_contain_text(FEES_URL)
    [website] = app_server.saved_websites()
    [fees_page] = [critical_page for critical_page in website.critical_pages if critical_page.url == FEES_URL]
    assert "$100" in fees_page.text_body


def test_a_critical_page_that_cannot_be_loaded_is_not_added(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests a critical page that can't be loaded shows why, and isn't added."""
    app_server.add_website(WEBSITE_URL)  # No fake page for the fees page, so it returns a 404
    card = _open_card(open_dashboard())

    card.locator(".new-critical-page-url").fill(FEES_URL)
    card.get_by_role("button", name="Add critical page").click()

    expect(card.locator(".critical-page-message")).to_contain_text("could not be loaded")
    [website] = app_server.saved_websites()
    assert [critical_page.url for critical_page in website.critical_pages] == [WEBSITE_URL]


def test_deleting_a_critical_page_asks_first(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests deleting a critical page asks for confirmation: Cancel keeps it, Delete removes it."""
    app_server.add_website(WEBSITE_URL, critical_pages=[FEES_URL])
    page = open_dashboard()
    dialog = page.locator("#confirm-dialog")

    _open_card(page).locator(".delete-critical-page-button").click()
    expect(dialog).to_be_visible()
    expect(page.locator("#confirm-dialog-title")).to_have_text("Delete this critical page?")
    expect(page.locator("#confirm-dialog-cancel")).to_be_focused()  # So pressing Enter cancels

    page.locator("#confirm-dialog-cancel").click()
    expect(dialog).to_be_hidden()
    expect(_card(page).locator(".critical-page-list")).to_contain_text(FEES_URL)

    _card(page).locator(".delete-critical-page-button").click()
    with page.expect_navigation():
        page.locator("#confirm-dialog-confirm").click()

    expect(_card(page).locator(".critical-page-list")).not_to_contain_text(FEES_URL)
    [website] = app_server.saved_websites()
    assert [critical_page.url for critical_page in website.critical_pages] == [WEBSITE_URL]


# ======================================
# Notification emails
# ======================================


def test_adding_a_notification_email_confirms_and_lists_it(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests an email added to a website is sent a confirmation, then listed on the card."""
    app_server.add_website(WEBSITE_URL)
    card = _open_card(open_dashboard())

    card.locator(".new-recipient-email").fill("team@example.com")
    card.get_by_role("button", name="Add notification email").click()

    expect(_card(card.page).locator(".recipient-list")).to_contain_text("team@example.com")
    assert [email.to for email in app_server.sent_emails.emails] == ["team@example.com"]
    [website] = app_server.saved_websites()
    assert [recipient.email for recipient in website.recipients] == ["team@example.com"]


def test_removing_a_notification_email_asks_first(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests removing an email asks for confirmation, then stops it receiving reports for the website."""
    app_server.add_website(WEBSITE_URL, recipients=["team@example.com"])
    page = open_dashboard()

    _open_card(page).locator(".delete-recipient-button").click()
    expect(page.locator("#confirm-dialog-title")).to_have_text("Remove team@example.com?")
    expect(page.locator("#confirm-dialog-confirm")).to_have_text("Remove")
    with page.expect_navigation():
        page.locator("#confirm-dialog-confirm").click()

    expect(_card(page).locator(".recipient-list")).to_have_count(0)
    [website] = app_server.saved_websites()
    assert website.recipients == []


# ======================================
# Scan settings
# ======================================


def test_saving_scan_settings(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests a website's scan settings are saved, kept after the reload, and an inactive website says so."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard()
    card = _open_card(page)

    card.locator(".scan-settings summary").click()
    settings = card.locator(".scan-settings-form")
    settings.locator(".scan-active").uncheck()
    settings.locator(".scan-delay").fill("1.5")
    settings.locator(".scan-concurrent").fill("2")
    settings.locator(".days-between-scans").fill("3")
    with page.expect_navigation():
        settings.get_by_role("button", name="Save Settings").click()

    [website] = app_server.saved_websites()
    assert (website.active, website.recommended_delay, website.recommended_concurrent, website.days_between_scans) == (
        False,
        1.5,
        2,
        3,
    )
    card = _card(page)
    expect(card.locator(".run-scan-button")).to_have_text(re.compile("Scan critical pages"))
    expect(card.locator(".website-notice")).to_be_visible()


def test_saving_one_setting_keeps_what_the_app_changed_since_the_page_loaded(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests changing one setting on a page loaded before the app switched the website off (e.g. it was found too
    large) only saves that setting, rather than switching the website back on with the page's old values."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard()
    with app_server.session() as session:
        [website] = app_server.saved_websites()
        handle_too_large(WebsiteService(session), website.id, max_pages=50_000)
        session.commit()

    card = _open_card(page)
    card.locator(".scan-settings summary").click()
    settings = card.locator(".scan-settings-form")
    settings.locator(".days-between-scans").fill("3")
    with page.expect_navigation():
        settings.get_by_role("button", name="Save Settings").click()

    [website] = app_server.saved_websites()
    assert (website.active, website.deactivated_reason, website.days_between_scans) == (False, "too_large", 3)


# ======================================
# Deleting a website
# ======================================


def test_deleting_a_website_needs_confirm_typed(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests deleting a website needs CONFIRM typed exactly: wrong text says so, Cancel keeps the website, and
    typing CONFIRM then Delete removes it."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard()
    delete_button, error = page.locator("#confirm-dialog-confirm"), page.locator("#confirm-dialog-error")

    card = _open_card(page)
    card.locator(".delete-website-button").click()
    expect(page.locator("#confirm-dialog-title")).to_have_text(f"Delete {card.get_attribute('data-website-name')}?")
    expect(page.locator("#confirm-dialog-input")).to_be_focused()
    expect(delete_button).to_be_disabled()

    page.locator("#confirm-dialog-input").fill("confirm")
    expect(error).to_be_visible()
    expect(error).to_have_text("Confirmation text does not match.")
    expect(delete_button).to_be_disabled()

    page.locator("#confirm-dialog-cancel").click()
    expect(page.locator("#confirm-dialog")).to_be_hidden()
    assert len(app_server.saved_websites()) == 1

    _card(page).locator(".delete-website-button").click()
    expect(error).to_be_hidden()  # Each time the dialog opens it starts empty
    page.locator("#confirm-dialog-input").fill("CONFIRM")
    expect(delete_button).to_be_enabled()
    with page.expect_navigation():
        delete_button.click()

    expect(page.locator(".website-card")).to_have_count(0)
    assert app_server.saved_websites() == []
