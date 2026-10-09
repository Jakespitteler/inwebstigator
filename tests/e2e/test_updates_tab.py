"""The Updates tab: what changed on each website since its last scan, and moving between the dashboard's tabs."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from playwright.sync_api import Locator, Page, expect
from sqlalchemy import select

from app.db.schema import DBCriticalPage
from tests.e2e.conftest import RunningApp

WEBSITE_URL: str = "https://example.com"


def _site_html(paragraphs: Sequence[str], links: Sequence[str] = (), documents: Sequence[str] = ()) -> str:
    """A simple page with a "Fees" heading, its paragraphs, then links to other pages and documents."""
    return (
        "<html><body><h2>Fees</h2>"
        + "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        + "".join(f'<a href="{href}">{href}</a>' for href in (*links, *documents))
        + "</body></html>"
    )


def _add_website(page: Page, app_server: RunningApp, html: str) -> None:
    """Adds the website through the app, so its first scan saves the page as it is now."""
    app_server.websites.set_page(WEBSITE_URL, html)
    response = page.request.post(f"{app_server.url}scanner/initial_scan", data={"url": WEBSITE_URL})
    assert response.ok, response.text()


def _scan(page: Page, app_server: RunningApp, html: str) -> None:
    """Changes the website's page, then scans it the way "Run scan" does."""
    app_server.websites.set_page(WEBSITE_URL, html)
    response = page.request.post(f"{app_server.url}scanner/run", form={"url": WEBSITE_URL})
    assert response.ok, response.text()


def _show_updates(page: Page, app_server: RunningApp) -> None:
    """Reloads the dashboard on the Updates tab, to show what the latest scan found.

    Going to "#updates" alone is a jump within the page, which would keep showing it as it was before the scan.
    """
    page.goto(f"{app_server.url}#updates")
    page.reload()


def _open(card: Locator) -> Locator:
    """Opens an update card, showing the changes inside it."""
    card.locator(":scope > .card-header .card-toggle").click()
    body = card.locator(":scope > .card-body")
    expect(body).to_be_visible()
    return body


def _change_items(section: Locator, kind: str) -> list[str]:
    """Returns the text of each added or removed item in a section, e.g. kind="added"."""
    return [text.strip() for text in section.locator(f".change-item-{kind} .change-item-text").all_inner_texts()]


def _section(body: Locator, heading: str) -> Locator:
    return body.locator(".change-section").filter(has=body.page.locator("h2", has_text=heading))


# ======================================
# No changes
# ======================================


def test_says_when_the_latest_scan_found_no_changes(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests the Updates tab says so when no website has changed."""
    app_server.add_website(WEBSITE_URL)
    page = open_dashboard("#updates")

    expect(page.locator("#updates-panel .empty-state")).to_have_text("No changes were detected in the latest scan.")
    expect(page.locator(".website-change-record")).to_have_count(0)


def test_a_first_scan_is_not_shown_as_changes(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests adding a website saves what's on it without listing it all as new on the Updates tab."""
    page = open_dashboard()
    _add_website(page, app_server, _site_html(["The application fee is $100."], links=["/apply"]))

    _show_updates(page, app_server)
    expect(page.locator(".website-change-record")).to_have_count(0)


# ======================================
# Every kind of change
# ======================================


def test_a_scan_shows_every_kind_of_change(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests a scan that finds changed, added and removed text, links, documents and pages shows each of them,
    with counts on the cards and the changed words highlighted."""
    for path in ("/apply", "/old-guide", "/contact"):
        app_server.websites.set_page(f"{WEBSITE_URL}{path}", f"<p>{path}</p>")
    page = open_dashboard()
    _add_website(
        page,
        app_server,
        _site_html(
            ["The application fee is $100 and is paid when you apply.", "Refunds are available within 14 days."],
            links=["/apply", "/old-guide"],
            documents=["/files/2025-fees.pdf"],
        ),
    )
    _scan(
        page,
        app_server,
        _site_html(
            ["The application fee is $120 and is paid when you apply.", "Concession card holders pay half the fee."],
            links=["/apply", "/contact"],
            documents=["/files/2026-fees.pdf"],
        ),
    )

    _show_updates(page, app_server)
    website_card = page.locator(".website-change-record")
    expect(website_card).to_have_count(1)
    expect(website_card.locator(".website-url")).to_have_text(WEBSITE_URL)
    expect(website_card.locator(".website-name")).to_have_text("Example")  # Its display name
    expect(website_card.locator(":scope > .card-header .change-summary")).to_contain_text("1 critical page changed")
    expect(website_card.locator(":scope > .card-header .change-summary")).to_contain_text("2 internal links")

    website_body = _open(website_card)
    internal_links = _section(website_body, "Internal links")
    assert _change_items(internal_links, "added") == [f"{WEBSITE_URL}/contact"]
    assert _change_items(internal_links, "removed") == [f"{WEBSITE_URL}/old-guide"]

    page_card = website_body.locator(".page-change-record")
    expect(page_card).to_have_count(1)
    summary = page_card.locator(":scope > .card-header .change-summary")
    for count in ("3 text changes", "2 links", "2 documents"):
        expect(summary).to_contain_text(count)

    page_body = _open(page_card)
    content_changed = _section(page_body, "Content changed")
    expect(content_changed.locator(".removed-word")).to_have_text(["$100"])
    expect(content_changed.locator(".added-word")).to_have_text(["$120"])

    text = _section(page_body, "Text")
    assert _change_items(text, "added") == ["Concession card holders pay half the fee."]
    assert _change_items(text, "removed") == ["Refunds are available within 14 days."]

    links = _section(page_body, "Links")
    assert _change_items(links, "added") == [f"{WEBSITE_URL}/contact"]
    assert _change_items(links, "removed") == [f"{WEBSITE_URL}/old-guide"]

    documents = _section(page_body, "Documents")
    assert _change_items(documents, "added") == ["2026-fees.pdf"]
    assert _change_items(documents, "removed") == ["2025-fees.pdf"]


def test_text_that_looks_like_html_never_reaches_the_dashboard(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests text on a monitored page that looks like HTML (e.g. an <img> tag written out) is left out when the page
    is read, so a website can never put code into the dashboard."""
    injected = '&lt;img src=x onerror="window.injected = true"&gt;'
    page = open_dashboard()
    _add_website(page, app_server, _site_html(["The fee is $100."]))
    _scan(page, app_server, _site_html(["The fee is $120.", f"Embed a picture with {injected}"]))

    _show_updates(page, app_server)
    page_body = _open(_open(page.locator(".website-change-record")).locator(".page-change-record"))

    expect(page_body).not_to_contain_text("onerror")
    expect(page.locator("#updates-panel img")).to_have_count(0)
    assert page.evaluate("window.injected") is None


def test_special_characters_are_shown_as_written(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests <, >, & and quotes on a monitored page are shown exactly as written, both in the highlighted words of
    changed text and in added text."""
    page = open_dashboard()
    _add_website(page, app_server, _site_html(['Fees under &lt; $100 &amp; marked "approx".', "Other text."]))
    _scan(
        page,
        app_server,
        _site_html(['Fees under &lt; $120 &amp; marked "approx".', "Other text.", "Note: 5 &gt; 3 &amp; 2 &lt; 4."]),
    )

    _show_updates(page, app_server)
    page_body = _open(_open(page.locator(".website-change-record")).locator(".page-change-record"))

    content_changed = _section(page_body, "Content changed")
    expect(content_changed).to_contain_text('Fees under < $100 & marked "approx".')
    expect(content_changed).to_contain_text('Fees under < $120 & marked "approx".')
    assert _change_items(_section(page_body, "Text"), "added") == ["Note: 5 > 3 & 2 < 4."]


# ======================================
# Order and remembering open cards
# ======================================


def test_most_recent_changes_come_first(open_dashboard: Callable[..., Page], app_server: RunningApp) -> None:
    """Tests the website whose changes were found most recently is listed first."""
    for url in ("https://older.example.com", "https://newer.example.com"):
        app_server.add_website(url)
    with app_server.session() as session:
        for url, changed_at in (
            ("https://older.example.com", datetime.now() - timedelta(days=2)),
            ("https://newer.example.com", datetime.now() - timedelta(hours=1)),
        ):
            critical_page = session.scalars(select(DBCriticalPage).where(DBCriticalPage.url == url)).one()
            critical_page.recent_links_added = [f"{url}/new"]
            critical_page.last_changed_at = changed_at
        session.commit()

    page = open_dashboard("#updates")

    expect(page.locator(".website-change-record .website-url")).to_have_text(
        ["https://newer.example.com", "https://older.example.com"]
    )


def test_an_open_update_card_stays_open_after_a_reload(
    open_dashboard: Callable[..., Page], app_server: RunningApp
) -> None:
    """Tests an update card opened on the Updates tab is still open after the dashboard reloads."""
    page = open_dashboard()
    _add_website(page, app_server, _site_html(["The fee is $100."]))
    _scan(page, app_server, _site_html(["The fee is $120."]))
    _show_updates(page, app_server)

    _open(page.locator(".website-change-record"))
    page.reload()

    expect(page.locator(".website-change-record > .card-body")).to_be_visible()


# ======================================
# Moving between tabs with the keyboard
# ======================================


def test_tabs_can_be_changed_with_the_arrow_keys(open_dashboard: Callable[..., Page]) -> None:
    """Tests the arrow, Home and End keys move between the Websites and Updates tabs, as tabs should."""
    page = open_dashboard()
    websites_tab, updates_tab = page.get_by_role("tab", name="Websites"), page.get_by_role("tab", name="Updates")

    websites_tab.focus()
    page.keyboard.press("ArrowRight")
    expect(updates_tab).to_be_focused()
    expect(updates_tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("#updates-panel")).to_be_visible()

    page.keyboard.press("ArrowLeft")
    expect(websites_tab).to_be_focused()
    expect(page.locator("#websites-panel")).to_be_visible()

    page.keyboard.press("End")
    expect(updates_tab).to_be_focused()
    page.keyboard.press("Home")
    expect(websites_tab).to_be_focused()
    expect(websites_tab).to_have_attribute("aria-selected", "true")
