"""Updates use the websites' current tags without changing or reloading their scan history."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect
from sqlalchemy import select

from app.db.schema import DBChange, DBScanRun, DBWebsite
from app.models.scan_run_models import ChangeKind, ScanStatus
from tests.e2e.conftest import API_HEADERS, RunningApp


def _save_website(app_server: RunningApp, name: str, slug: str, *, scanned: bool = True) -> str:
    """Saves a website and optional scan directly, without requesting a real page or sending email."""
    url = f"https://{slug}.example/"
    app_server.add_website(url, card_title=name)
    with app_server.session() as session:
        website = session.scalars(select(DBWebsite).where(DBWebsite.url == url)).one()
        if scanned:
            website.scan_runs.append(
                DBScanRun(
                    scanned_at=datetime.now(UTC),
                    status=ScanStatus.SUCCESS,
                    changes=[DBChange(position=0, kind=ChangeKind.INTERNAL_LINK_ADDED, url=f"{url}new")],
                )
            )
        session.commit()
        return str(website.id)


def _tag(page: Page, app_server: RunningApp, name: str, *website_ids: str) -> str:
    response = page.request.post(f"{app_server.url}tags", data={"name": name}, headers=API_HEADERS)
    assert response.ok, response.text()
    tag_id: str = response.json()["id"]
    for website_id in website_ids:
        response = page.request.post(f"{app_server.url}websites/{website_id}/tags/{tag_id}", headers=API_HEADERS)
        assert response.ok, response.text()
    return tag_id


def _history(page: Page, slug: str) -> Locator:
    return page.locator(f'.website-change-record[data-website-url="https://{slug}.example/"]')


def _website(page: Page, website_id: str) -> Locator:
    return page.locator(f'#monitored-website-list .website-card[data-website-id="{website_id}"]')


def _filter(page: Page, name: str, *, updates: bool = True) -> Locator:
    filters = "#updates-tag-filters" if updates else "#website-tag-filters"
    return page.locator(filters).get_by_role("button", name=name, exact=True)


def test_updates_filter_multiple_tags_and_keep_untagged_sites_in_all(
    page: Page, open_dashboard: Callable[[str], Page], app_server: RunningApp
) -> None:
    news = _save_website(app_server, "Daily News", "news")
    university = _save_website(app_server, "University", "university")
    _save_website(app_server, "No tags", "untagged")
    waiting = _save_website(app_server, "Waiting for a scan", "waiting", scanned=False)
    _tag(page, app_server, "News", news)
    _tag(page, app_server, "Study", news, university)
    _tag(page, app_server, "Shopping", waiting)
    open_dashboard("#updates")

    expect(page.locator(".website-change-record:visible")).to_have_count(3)
    expect(page.locator("#updates-result-count")).to_have_text("3 of 3 websites")
    expect(_history(page, "untagged")).to_be_visible()
    expect(_history(page, "waiting")).to_have_count(0)

    _filter(page, "Study").click()
    expect(page.locator(".website-change-record:visible")).to_have_count(2)
    expect(_history(page, "news")).to_be_visible()
    expect(_history(page, "university")).to_be_visible()
    expect(_history(page, "untagged")).to_be_hidden()
    expect(page.locator("#updates-result-count")).to_have_text("2 of 3 websites")

    _filter(page, "News").click()
    expect(page.locator(".website-change-record:visible")).to_have_count(1)
    expect(_history(page, "news")).to_be_visible()
    expect(_filter(page, "News")).to_have_attribute("aria-pressed", "true")

    _filter(page, "Shopping").click()
    expect(page.locator(".website-change-record:visible")).to_have_count(0)
    expect(page.locator("#updates-result-count")).to_have_text("0 of 3 websites")
    expect(page.locator("#no-matching-updates")).to_be_visible()
    expect(page.locator("#no-matching-updates")).to_have_text("No scan history matches this tag.")

    _filter(page, "All").click()
    expect(page.locator(".website-change-record:visible")).to_have_count(3)
    expect(page.locator("#no-matching-updates")).to_be_hidden()
    expect(page.locator(".scan-change-record")).to_have_count(3)
    assert app_server.websites.requested_urls == []
    assert app_server.sent_emails.emails == []


def test_update_filters_are_independent_and_preserve_open_history_after_reload(
    page: Page, open_dashboard: Callable[[str], Page], app_server: RunningApp
) -> None:
    news = _save_website(app_server, "Daily News", "news")
    university = _save_website(app_server, "University", "university")
    _tag(page, app_server, "News", news)
    _tag(page, app_server, "Study", university)
    open_dashboard("#updates")
    _filter(page, "News").click()
    history = _history(page, "news")
    history.locator(":scope > .card-header .card-toggle").click()
    expect(history.locator(":scope > .card-body")).to_be_visible()

    page.locator("#websites-tab").click()
    _filter(page, "Study", updates=False).click()
    expect(_website(page, university)).to_be_visible()
    expect(_website(page, news)).to_be_hidden()
    page.locator("#updates-tab").click()
    expect(history).to_be_visible()
    expect(_history(page, "university")).to_be_hidden()
    expect(history.locator(":scope > .card-body")).to_be_visible()

    _filter(page, "Study").click()
    expect(history).to_be_hidden()
    _filter(page, "News").click()
    expect(history.locator(":scope > .card-body")).to_be_visible()
    page.reload()
    expect(_filter(page, "News")).to_have_attribute("aria-pressed", "true")
    expect(history.locator(":scope > .card-body")).to_be_visible()
    expect(_history(page, "university")).to_be_hidden()
    page.locator("#websites-tab").click()
    expect(_filter(page, "Study", updates=False)).to_have_attribute("aria-pressed", "true")
    expect(_website(page, university)).to_be_visible()
    expect(_website(page, news)).to_be_hidden()


def test_assigning_and_removing_tags_updates_history_on_the_same_page(
    page: Page, open_dashboard: Callable[[str], Page], app_server: RunningApp
) -> None:
    news = _save_website(app_server, "Daily News", "news")
    university = _save_website(app_server, "University", "university")
    _tag(page, app_server, "News", university)
    open_dashboard("#updates")
    _filter(page, "News").click()
    expect(_history(page, "news")).to_be_hidden()
    expect(_history(page, "university")).to_be_visible()

    page.locator("#websites-tab").click()
    card = _website(page, news)
    card.locator(".card-toggle").click()
    card.locator(".add-website-tags").click()
    choice = page.get_by_role("checkbox", name="News", exact=True)
    expect(choice).to_be_enabled()
    choice.check()
    expect(page.locator("#tag-form-message")).to_contain_text("News added.")
    expect(page.get_by_role("button", name="Delete News tag", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Close tags", exact=True).click()
    page.locator("#updates-tab").click()
    expect(page.locator(".website-change-record:visible")).to_have_count(2)
    expect(page.locator("#updates-result-count")).to_have_text("2 of 2 websites")

    page.locator("#websites-tab").click()
    card.get_by_role("button", name="Remove News tag from Daily News", exact=True).click()
    expect(card.locator(".website-tag")).to_have_count(0)
    page.locator("#updates-tab").click()
    expect(_history(page, "news")).to_be_hidden()
    expect(_history(page, "university")).to_be_visible()
    expect(page.locator("#updates-result-count")).to_have_text("1 of 2 websites")
    expect(_filter(page, "News")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".scan-change-record")).to_have_count(2)


def test_renaming_and_deleting_the_selected_tag_updates_both_tabs(
    page: Page, open_dashboard: Callable[[str], Page], app_server: RunningApp
) -> None:
    news = _save_website(app_server, "Daily News", "news")
    university = _save_website(app_server, "University", "university")
    _tag(page, app_server, "News", news, university)
    open_dashboard("#updates")
    _filter(page, "News").click()
    page.locator("#websites-tab").click()
    page.get_by_role("button", name="Manage tags", exact=True).click()
    page.get_by_role("button", name="Rename News tag", exact=True).click()
    page.get_by_label("Rename tag", exact=True).fill("Media")
    page.locator("#save-tag-button").click()
    expect(page.locator("#tag-form-message")).to_contain_text("Media renamed.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    expect(_filter(page, "News", updates=False)).to_have_count(0)
    expect(_filter(page, "Media", updates=False)).to_be_visible()
    page.locator("#updates-tab").click()
    expect(_filter(page, "News")).to_have_count(0)
    expect(_filter(page, "Media")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".website-change-record:visible")).to_have_count(2)

    page.locator("#websites-tab").click()
    page.get_by_role("button", name="Manage tags", exact=True).click()
    page.get_by_role("button", name="Delete Media tag", exact=True).click()
    page.get_by_role("button", name="Delete tag", exact=True).click()
    expect(page.locator("#tag-form-message")).to_contain_text("Media deleted.")
    page.get_by_role("button", name="Close tags", exact=True).click()
    page.locator("#updates-tab").click()
    expect(_filter(page, "Media")).to_have_count(0)
    expect(_filter(page, "All")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".website-change-record:visible")).to_have_count(2)
    expect(page.locator(".scan-change-record")).to_have_count(2)
    expect(page.locator("#no-matching-updates")).to_be_hidden()
    page.reload()
    expect(_filter(page, "All")).to_have_attribute("aria-pressed", "true")
    assert len(app_server.saved_websites()) == 2


def test_an_empty_history_keeps_its_empty_state_when_filtering(
    page: Page, open_dashboard: Callable[[str], Page], app_server: RunningApp
) -> None:
    waiting = _save_website(app_server, "Waiting for a scan", "waiting", scanned=False)
    _tag(page, app_server, "News", waiting)
    open_dashboard("#updates")
    _filter(page, "News").click()
    expect(page.locator("#updates-panel .empty-state")).to_have_text("No websites have been scanned yet.")
    expect(page.locator("#updates-result-count")).to_have_text("0 of 0 websites")
    expect(page.locator("#no-matching-updates")).to_be_hidden()
    expect(page.locator(".website-change-record")).to_have_count(0)


@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (1280, "dark"), (390, "light"), (390, "dark")])
def test_update_tag_filters_fit_both_themes_and_mobile(
    page: Page,
    open_dashboard: Callable[[str], Page],
    app_server: RunningApp,
    width: int,
    theme: str,
    tmp_path: Path,
) -> None:
    news = _save_website(app_server, "Daily News", "news")
    shop = _save_website(app_server, "Shop", "shop")
    _tag(page, app_server, "News", news)
    _tag(page, app_server, "Shopping", shop)
    _tag(page, app_server, "Research and official announcements", news)
    page.set_viewport_size({"width": width, "height": 900})
    open_dashboard("#updates")
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    expect(page.locator("#updates-tag-filters")).to_be_visible()
    _filter(page, "News").click()
    expect(_filter(page, "News")).to_have_attribute("aria-pressed", "true")
    expect(_history(page, "news")).to_be_visible()
    expect(_history(page, "shop")).to_be_hidden()
    expect(page.locator("#updates-result-count")).to_have_text("1 of 2 websites")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"update-tags-{theme}-{width}.png"), full_page=True)
