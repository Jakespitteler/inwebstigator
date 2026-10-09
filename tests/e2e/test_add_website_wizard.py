"""The Add Website wizard: moving through its steps, checking what's entered, and adding a website."""

import time
from collections.abc import Callable

from playwright.sync_api import Locator, Page, expect

from app.scanner import queued_crawls
from tests.e2e.conftest import RunningApp

WEBSITE_URL: str = "https://example.com"
WEBSITE_HTML: str = '<html><body><h1>Example</h1><p>Welcome.</p><a href="/fees">Fees</a></body></html>'
FEES_URL: str = "https://example.com/fees"
FEES_HTML: str = "<html><body><h1>Fees</h1><p>The fee is $100.</p></body></html>"


def _step(page: Page, number: int) -> Locator:
    return page.locator(f'.guide-step[data-step="{number}"]')


def _expect_on_step(page: Page, number: int) -> None:
    """Checks only the given step of the wizard is showing, and its dot is marked as the current step."""
    for step in range(1, 5):
        if step == number:
            expect(_step(page, step)).to_be_visible()
        else:
            expect(_step(page, step)).to_be_hidden()
    expect(page.locator(f'.guide-dot[data-step="{number}"]')).to_have_attribute("aria-current", "step")


def _review(page: Page) -> dict[str, str]:
    """Returns the review step's rows, e.g. {"Website": "https://example.com", ...}."""
    labels = page.locator("#guide-review dt").all_inner_texts()
    values = page.locator("#guide-review dd").all_inner_texts()
    return dict(zip(labels, values, strict=True))


def _serve_example_website(app_server: RunningApp) -> None:
    app_server.websites.set_page(WEBSITE_URL, WEBSITE_HTML)
    app_server.websites.set_page(FEES_URL, FEES_HTML)


# ======================================
# Moving through the steps
# ======================================


def test_next_needs_a_website_address(open_dashboard: Callable[..., Page]) -> None:
    """Tests Next won't leave the first step until a website address is entered."""
    page = open_dashboard()

    page.locator("#guide-next").click()

    _expect_on_step(page, 1)
    assert page.locator("#website-url").evaluate("input => input.validity.valueMissing")


def test_stepping_forwards_and_back_through_the_wizard(open_dashboard: Callable[..., Page]) -> None:
    """Tests Next and Back move one step at a time, Back is off on the first step, and the last step has
    Add Website in place of Next."""
    page = open_dashboard()
    back, next_step, submit = page.locator("#guide-back"), page.locator("#guide-next"), page.locator("#guide-submit")

    expect(back).to_be_disabled()
    page.locator("#website-url").fill(WEBSITE_URL)

    for step in (2, 3, 4):
        next_step.click()
        _expect_on_step(page, step)

    expect(next_step).to_be_hidden()
    expect(submit).to_be_visible()

    back.click()
    _expect_on_step(page, 3)
    expect(next_step).to_be_visible()
    expect(submit).to_be_hidden()


def test_step_dots_jump_between_steps_but_not_past_a_missing_address(open_dashboard: Callable[..., Page]) -> None:
    """Tests the numbered dots jump straight to a step, but can't skip a step with something missing."""
    page = open_dashboard()

    page.locator('.guide-dot[data-step="4"]').click()
    _expect_on_step(page, 1)

    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator('.guide-dot[data-step="3"]').click()
    _expect_on_step(page, 3)

    page.locator('.guide-dot[data-step="1"]').click()
    _expect_on_step(page, 1)


def test_enter_moves_to_the_next_step_instead_of_adding_the_website(open_dashboard: Callable[..., Page]) -> None:
    """Tests pressing Enter in a field goes to the next step, rather than submitting the form early."""
    page = open_dashboard()

    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator("#website-url").press("Enter")

    _expect_on_step(page, 2)
    expect(page.locator("#add-website-progress")).to_be_hidden()


def test_scan_settings_start_at_the_defaults(open_dashboard: Callable[..., Page]) -> None:
    """Tests the scan settings step is filled in with the app's default settings."""
    page = open_dashboard()
    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator('.guide-dot[data-step="3"]').click()

    # Compared as numbers, as the page may write them as e.g. "1.0"
    assert float(page.locator("#new-website-days").input_value()) == 1
    assert float(page.locator("#new-website-delay").input_value()) == 0.5
    assert float(page.locator("#new-website-concurrent").input_value()) == 5


# ======================================
# Reviewing what was entered
# ======================================


def test_review_lists_everything_entered_including_extra_fields(open_dashboard: Callable[..., Page]) -> None:
    """Tests "+ Add another" adds more page and email fields, and the review step lists every filled one."""
    page = open_dashboard()
    page.locator("#website-url").fill(WEBSITE_URL)

    page.locator("#add-critical-page-input").click()
    critical_pages = page.locator(".critical-page-input")
    expect(critical_pages).to_have_count(2)
    critical_pages.nth(0).fill(FEES_URL)
    critical_pages.nth(1).fill("https://example.com/dates")

    page.locator("#guide-next").click()
    page.locator("#add-recipient-email-input").click()
    emails = page.locator(".recipient-email-input")
    expect(emails).to_have_count(2)
    emails.nth(0).fill("first@example.com")
    emails.nth(1).fill("second@example.com")

    page.locator("#guide-next").click()
    page.locator("#new-website-days").fill("3")
    page.locator("#guide-next").click()

    assert _review(page) == {
        "Website": WEBSITE_URL,
        "Critical pages": f"{FEES_URL}, https://example.com/dates",
        "Notification emails": "first@example.com, second@example.com",
        "Days between scans": "3",
        "Request delay": "0.5 seconds",
        "Concurrent requests": "5",
    }


def test_review_says_none_for_optional_fields_left_empty(open_dashboard: Callable[..., Page]) -> None:
    """Tests the review step shows "None" for critical pages and emails that weren't given."""
    page = open_dashboard()
    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator('.guide-dot[data-step="4"]').click()

    review = _review(page)
    assert review["Critical pages"] == "None"
    assert review["Notification emails"] == "None"


# ======================================
# Adding the website
# ======================================


def test_adding_a_website_saves_it_and_shows_its_card(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests finishing the wizard runs the first scan, saves the website with its settings, emails its
    recipient, and shows the website's card with the wizard tucked behind "+ Add website"."""
    _serve_example_website(app_server)
    page = open_dashboard()

    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator(".critical-page-input").first.fill(FEES_URL)
    page.locator("#guide-next").click()
    page.locator(".recipient-email-input").first.fill("team@example.com")
    page.locator("#guide-next").click()
    page.locator("#new-website-days").fill("2")
    page.locator("#guide-next").click()
    page.locator("#guide-submit").click()

    expect(page.locator(".website-card")).to_have_count(1)
    expect(page.locator(".website-card")).to_contain_text("example.com")
    expect(page.locator("#add-website-form")).to_be_hidden()
    expect(page.locator("#add-website-toggle")).to_have_text("+ Add website")
    expect(page.locator(".guide-intro")).to_have_count(0)

    [website] = app_server.saved_websites()
    assert website.url == WEBSITE_URL
    assert website.days_between_scans == 2
    assert {page.url for page in website.critical_pages} == {WEBSITE_URL, FEES_URL}
    assert [recipient.email for recipient in website.recipients] == ["team@example.com"]
    assert {(email.to, email.subject) for email in app_server.sent_emails.emails} == {
        ("team@example.com", "Email address added to website monitoring"),
        ("team@example.com", "Website monitoring started"),
    }


def test_a_website_typed_without_https_is_added(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests a website can be added by typing just its domain, e.g. "example.com"."""
    _serve_example_website(app_server)
    page = open_dashboard()

    page.locator("#website-url").fill("example.com")
    page.locator('.guide-dot[data-step="4"]').click()
    page.locator("#guide-submit").click()

    expect(page.locator(".website-card")).to_have_count(1)
    assert [website.url for website in app_server.saved_websites()] == [WEBSITE_URL]


def test_a_website_that_cannot_be_loaded_is_not_added(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests a website that can't be loaded shows why, brings the wizard back with everything still filled in,
    and saves nothing."""
    page = open_dashboard()  # No fake page for the website, so it returns a 404

    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator('.guide-dot[data-step="4"]').click()
    page.locator("#guide-submit").click()

    expect(page.locator("#website-form-message")).to_have_text(
        f"{WEBSITE_URL} could not be loaded. Check it exists and the URL is correct."
    )
    expect(page.locator("#add-website-form")).to_be_visible()
    expect(page.locator("#add-website-progress")).to_be_hidden()
    _expect_on_step(page, 4)
    expect(page.locator("#website-url")).to_have_value(WEBSITE_URL)
    expect(page.locator("#guide-submit")).to_be_enabled()
    assert app_server.saved_websites() == []


def test_cancelling_the_first_scan_does_not_add_the_website(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests the "Scanning..." line shows while the first scan runs, and cancelling it adds nothing."""
    _serve_example_website(app_server)
    page = open_dashboard()
    page.locator("#website-url").fill(WEBSITE_URL)
    page.locator('.guide-dot[data-step="4"]').click()

    app_server.websites.delay_seconds = 0.5  # Slow the scan down enough to cancel it
    page.locator("#guide-submit").click()

    expect(page.locator("#add-website-form")).to_be_hidden()
    expect(page.locator("#add-website-progress-status")).to_have_text("Scanning example.com…")

    # Cancel once the scan has started, as before then there is nothing to cancel yet
    deadline = time.monotonic() + 10
    while WEBSITE_URL not in queued_crawls:
        assert time.monotonic() < deadline, "The first scan never started"
        time.sleep(0.05)
    page.locator("#guide-cancel-scan").click()

    expect(page.locator("#website-form-message")).to_have_text("Scan cancelled. The website was not added.")
    expect(page.locator("#add-website-form")).to_be_visible()
    assert app_server.saved_websites() == []


# ======================================
# Once websites exist
# ======================================


def test_add_website_button_opens_and_closes_the_wizard(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests that once a website exists the wizard starts closed, and "+ Add website" opens it at the first step
    with the address field ready to type in, then closes it again."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard()
    toggle, wizard = page.locator("#add-website-toggle"), page.locator("#add-website-form")

    expect(wizard).to_be_hidden()
    expect(toggle).to_have_text("+ Add website")
    expect(toggle).to_have_attribute("aria-expanded", "false")

    toggle.click()
    expect(wizard).to_be_visible()
    expect(toggle).to_have_text("Close")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#website-url")).to_be_focused()
    _expect_on_step(page, 1)

    toggle.click()
    expect(wizard).to_be_hidden()
    expect(toggle).to_have_text("+ Add website")
