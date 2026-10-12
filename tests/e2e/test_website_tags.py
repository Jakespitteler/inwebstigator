import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.conftest import RunningApp


def _card(page: Page, name: str) -> Locator:
    return page.locator("#monitored-website-list .website-card").filter(
        has=page.locator(".website-name").filter(has_text=re.compile(f"^{re.escape(name)}$"))
    )


def _create_tags(page: Page, *names: str) -> None:
    page.get_by_role("button", name="Manage tags", exact=True).click()
    for name in names:
        field = page.get_by_label("New tag", exact=True)
        expect(field).to_be_enabled()
        field.fill(name)
        page.locator("#save-tag-button").click()
        expect(page.locator("#tag-form-message")).to_contain_text(f"{name} created.")
    page.get_by_role("button", name="Close tags", exact=True).click()


def _assign(page: Page, name: str, *tags: str) -> None:
    card = _card(page, name)
    toggle = card.locator(".card-toggle")
    was_expanded = toggle.get_attribute("aria-expanded") == "true"
    if not was_expanded:
        toggle.click()
    card.get_by_role("button", name=f"Edit tags for {name}", exact=True).click()
    for tag in tags:
        choice = page.get_by_role("checkbox", name=tag, exact=True)
        expect(choice).to_be_enabled()
        choice.check()
        expect(page.locator("#tag-form-message")).to_contain_text(f"{tag} added.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    if not was_expanded:
        toggle.click()


def test_multiple_tags_combine_with_search_and_survive_reload(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
) -> None:
    app_server.add_website("https://news.example/culture/", card_title="Daily News")
    app_server.add_website("https://university.example/", card_title="University")
    app_server.add_website("https://shop.example/", card_title="Shop")
    page = open_dashboard("")
    _create_tags(page, "News", "Study", "Shopping")
    _assign(page, "Daily News", "News", "Study")
    _assign(page, "University", "Study")
    _assign(page, "Shop", "Shopping")
    expect(_card(page, "Daily News").locator(".website-tag")).to_have_count(2)

    page.locator("#website-tag-filters").get_by_role("button", name="Study", exact=True).click()
    expect(page.locator(".website-card:visible")).to_have_count(2)
    search = page.get_by_role("searchbox", name="Search monitored websites")
    search.fill("CULTURE")
    expect(_card(page, "Daily News")).to_be_visible()
    expect(_card(page, "University")).to_be_hidden()
    expect(page.locator("#website-result-count")).to_have_text("1 of 3 websites")
    page.reload()
    expect(search).to_have_value("CULTURE")
    expect(page.locator("#website-tag-filters").get_by_role("button", name="Study", exact=True)).to_have_attribute(
        "aria-pressed",
        "true",
    )
    expect(page.locator(".website-card:visible")).to_have_count(1)
    search.fill("no such site")
    expect(page.locator("#no-matching-websites")).to_be_visible()
    search.fill("")
    page.locator("#website-tag-filters").get_by_role("button", name="All", exact=True).click()
    search.fill("shopping")
    expect(_card(page, "Shop")).to_be_visible()
    expect(page.locator(".website-card:visible")).to_have_count(1)
    search.fill("")
    assert app_server.websites.requested_urls == []
    assert app_server.sent_emails.emails == []


def test_tags_can_be_created_on_a_card_and_removed_without_losing_form_edits(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
) -> None:
    app_server.add_website("https://news.example/", card_title="Daily News")
    page = open_dashboard("")
    card = _card(page, "Daily News")
    expect(card.locator(".website-card-header .website-tag-row")).to_have_count(0)
    expect(card.locator(".website-summary .website-tag-row")).to_be_hidden()
    card.locator(".card-toggle").click()
    expect(card.locator(".website-summary .website-tag-row")).to_be_visible()
    card.locator(".new-critical-page-url").fill("https://news.example/unfinished")
    card.locator(".add-website-tags").click()
    field = page.get_by_label("New tag", exact=True)
    expect(field).to_be_enabled()
    field.fill("News")
    page.locator("#save-tag-button").click()
    expect(page.locator("#tag-form-message")).to_contain_text("created and added")
    expect(page.get_by_role("checkbox", name="News", exact=True)).to_be_checked()
    # Checkbox removal removes only this website's assignment.
    page.get_by_role("checkbox", name="News", exact=True).uncheck()
    expect(page.locator("#tag-form-message")).to_contain_text("News removed.")
    expect(card.locator(".website-tag")).to_have_count(0)
    page.get_by_role("checkbox", name="News", exact=True).check()
    expect(page.locator("#tag-form-message")).to_contain_text("News added.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    expect(card.locator(".new-critical-page-url")).to_have_value("https://news.example/unfinished")
    expect(card.locator(".card-body")).to_be_visible()
    expect(card.locator(".add-website-tags")).to_be_focused()
    card.locator(".card-toggle").click()
    expect(card.locator(".website-tag-row")).to_be_hidden()
    card.locator(".card-toggle").click()
    expect(card.locator(".website-tag-row")).to_be_visible()
    expect(card.locator(".new-critical-page-url")).to_have_value("https://news.example/unfinished")
    page.locator("#website-tag-filters").get_by_role("button", name="News", exact=True).click()
    card.get_by_role("button", name="Remove News tag from Daily News", exact=True).click()
    expect(card).to_be_hidden()
    expect(page.locator("#no-matching-websites")).to_be_visible()
    expect(page.locator("#website-tag-filters").get_by_role("button", name="News", exact=True)).to_be_focused()
    page.locator("#website-tag-filters").get_by_role("button", name="All", exact=True).click()
    page.reload()
    expect(card.locator(".website-tag")).to_have_count(0)
    expect(page.locator("#website-tag-filters").get_by_role("button", name="News", exact=True)).to_be_visible()


def test_card_removal_keeps_shared_tags_on_other_websites(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
) -> None:
    app_server.add_website("https://news.example/", card_title="Daily News")
    app_server.add_website("https://university.example/", card_title="University")
    page = open_dashboard("")
    _create_tags(page, "News")
    _assign(page, "Daily News", "News")
    _assign(page, "University", "News")
    card = _card(page, "Daily News")
    other = _card(page, "University")
    card.locator(".card-toggle").click()
    card.locator(".add-website-tags").click()
    expect(page.locator("#tag-dialog-description")).to_contain_text("for this website")
    expect(page.get_by_role("button", name="Delete News tag", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Rename News tag", exact=True)).to_have_count(0)
    choice = page.get_by_role("checkbox", name="News", exact=True)
    expect(choice).to_be_enabled()
    choice.uncheck()
    expect(page.locator("#tag-form-message")).to_contain_text("News removed.")
    expect(card.locator(".website-tag")).to_have_count(0)
    expect(other.locator(".website-tag-name")).to_have_text("News")
    choice.check()
    expect(page.locator("#tag-form-message")).to_contain_text("News added.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    card.get_by_role("button", name="Remove News tag from Daily News", exact=True).click()
    expect(card.locator(".website-tag")).to_have_count(0)
    expect(other.locator(".website-tag-name")).to_have_text("News")
    page.reload()
    expect(card.locator(".website-tag")).to_have_count(0)
    expect(other.locator(".website-tag-name")).to_have_text("News")
    expect(page.locator("#website-tag-filters").get_by_role("button", name="News", exact=True)).to_be_visible()
    page.get_by_role("button", name="Manage tags", exact=True).click()
    expect(page.get_by_role("button", name="Delete News tag", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Rename News tag", exact=True)).to_be_visible()


def test_popup_can_rename_delete_and_handle_duplicate_names(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
) -> None:
    app_server.add_website("https://news.example/", card_title="Daily News")
    page = open_dashboard("")
    _create_tags(page, "News")
    _assign(page, "Daily News", "News")
    page.locator("#website-tag-filters").get_by_role("button", name="News", exact=True).click()
    page.get_by_role("button", name="Manage tags", exact=True).click()
    field = page.get_by_label("New tag", exact=True)
    expect(field).to_be_enabled()
    field.fill(" news ")
    page.locator("#save-tag-button").click()
    expect(page.locator("#tag-form-message")).to_contain_text("already exists")
    expect(field).to_be_focused()
    page.get_by_role("button", name="Rename News tag", exact=True).click()
    page.get_by_label("Rename tag", exact=True).fill("Media")
    page.locator("#save-tag-button").click()
    expect(page.locator("#tag-form-message")).to_contain_text("Media renamed.")
    expect(_card(page, "Daily News").locator(".website-tag-name")).to_have_text("Media")
    page.get_by_role("button", name="Delete Media tag", exact=True).click()
    page.locator("#confirm-dialog-cancel").click()
    expect(page.get_by_role("button", name="Delete Media tag", exact=True)).to_be_visible()
    page.get_by_role("button", name="Delete Media tag", exact=True).click()
    page.get_by_role("button", name="Delete tag", exact=True).click()
    expect(page.locator("#tag-form-message")).to_contain_text("Media deleted.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    expect(_card(page, "Daily News")).to_be_visible()
    expect(_card(page, "Daily News").locator(".website-tag")).to_have_count(0)
    expect(page.locator("#website-tag-filters button.active")).to_have_text("All")
    assert len(app_server.saved_websites()) == 1


def test_failed_assignment_keeps_previous_tags_and_allows_retry(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
) -> None:
    app_server.add_website("https://news.example/", card_title="Daily News")
    page = open_dashboard("")
    _create_tags(page, "News")
    page.route(
        "**/websites/*/tags/*",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body='{"detail":"Please try again."}',
        ),
    )
    card = _card(page, "Daily News")
    card.locator(".card-toggle").click()
    card.locator(".add-website-tags").click()
    choice = page.get_by_role("checkbox", name="News", exact=True)
    expect(choice).to_be_enabled()
    choice.check()
    expect(page.locator("#tag-form-message")).to_contain_text("Please try again")
    expect(choice).not_to_be_checked()
    expect(choice).to_be_enabled()
    page.unroute("**/websites/*/tags/*")
    choice.check()
    expect(page.locator("#tag-form-message")).to_contain_text("News added.")
    expect(choice).to_be_checked()


@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (1280, "dark"), (390, "light"), (390, "dark")])
def test_tag_and_search_layout_fits_both_themes_and_mobile(
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
    width: int,
    theme: str,
    tmp_path: Path,
) -> None:
    app_server.add_website("https://news.example/", card_title="Daily News", recipients=["test@example.com"])
    app_server.add_website("https://shop.example/", card_title="Shop", recipients=["shop@example.com"])
    page = open_dashboard("")
    page.set_viewport_size({"width": width, "height": 900})
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    _create_tags(page, "News", "Shopping", "Research and official announcements")
    _assign(page, "Daily News", "News", "Research and official announcements")
    _assign(page, "Shop", "Shopping")
    card = _card(page, "Daily News")
    expect(card.locator(".website-tag-row")).to_be_hidden()
    expect(page.get_by_role("searchbox", name="Search monitored websites")).to_be_visible()
    expect(card.locator(".run-scan-button")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"website-tags-{theme}-{width}.png"), full_page=True)
    card.locator(".card-toggle").click()
    expect(card.locator(".website-summary .website-tag-row")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if width > 560:
        count = card.locator(".website-summary-info > p").bounding_box()
        tags = card.locator(".website-tag-row").bounding_box()
        assert count is not None and tags is not None
        assert tags["x"] > count["x"] + count["width"]
        assert abs(tags["y"] - count["y"]) < 5
    page.screenshot(path=str(tmp_path / f"website-tag-details-{theme}-{width}.png"), full_page=True)
    card.locator(".card-toggle").click()
    page.get_by_role("button", name="Manage tags", exact=True).click()
    expect(page.get_by_label("New tag", exact=True)).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"tag-popup-{theme}-{width}.png"))
