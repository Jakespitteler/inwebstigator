from collections.abc import Callable

import httpx2
import pytest
from pydantic import HttpUrl

from app.backend.scanning.critical_page_checks import (
    STAND_IN_FAILURES_BEFORE_ACCEPTING,
    STAND_IN_PAGE_REASON,
    check_critical_page,
    check_critical_pages,
)
from app.models.critical_page_models import CriticalPageRead
from app.models.scan_result_models import PAGE_CHANGE_FIELDS, CriticalPageScanResult
from tests.conftest import RequestHandler

# ======================================
# check_critical_page
# ======================================


@pytest.mark.anyio
async def test_check_critical_page_with_changes(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests that a critical page successfully flags added links and text changes against the mock HTML."""
    initial_page = test_critical_page.model_copy(
        update={
            "text_body": "<html><body><p>Old Content</p></body></html>",
            "links": [f"{test_critical_page.url}old-link"],
        }
    )
    async with mock_client_factory(website_handler) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, initial_page)
    assert page_result
    assert page_result.has_changes
    assert page_result.url == initial_page.url
    assert page_result.links_added == [
        HttpUrl("https://www.test_website.com/about"),
        HttpUrl("https://www.test_website.com/new-link"),
    ]
    assert page_result.links_removed == [HttpUrl(f"{test_critical_page.url}old-link")]
    assert [block.text for block in page_result.text_removed or []] == ["Old Content"]
    assert page_result.text_body is not None and "Updated Critical Page Content" in page_result.text_body


@pytest.mark.anyio
async def test_a_watched_pages_links_are_resolved_against_its_base(
    test_critical_page: CriticalPageRead, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient]
):
    """Tests the links in a watched page's main content are resolved against the page's <base href>, which is in its
    <head>, rather than against the page's own address."""
    html = (
        '<html><head><base href="https://www.test_website.com/au/"></head><body><a href="fees">Fees</a></body></html>'
    )

    async with mock_client_factory(lambda request: httpx2.Response(200, html=html)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, test_critical_page, init=True)

    assert page_result is not None
    assert page_result.links == [HttpUrl("https://www.test_website.com/au/fees")]


@pytest.mark.anyio
async def test_check_critical_page_no_changes(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests a page re-scanned with the same content as its saved baseline returns nothing."""
    new_page = test_critical_page.model_copy(update={"text_body": None, "links": None, "documents": None})

    async with mock_client_factory(website_handler) as client:
        baseline: CriticalPageScanResult | None = await check_critical_page(client, new_page)
        assert baseline is not None

        synced_page = new_page.model_copy(
            update={"text_body": baseline.text_body, "links": baseline.links, "documents": baseline.documents}
        )
        assert await check_critical_page(client, synced_page) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stored_field", "removed_field", "removed_url"),
    [
        ("documents", "documents_removed", "https://www.test_website.com/files/policy.pdf"),
        ("links", "links_removed", "https://www.test_website.com/old-page"),
    ],
    ids=["last-document", "last-link"],
)
async def test_check_critical_page_detects_last_link_or_document_removed(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    stored_field: str,
    removed_field: str,
    removed_url: str,
):
    """Tests removing a page's only link or document is reported and saved, not ignored as "no change"."""
    html = "<html><body><p>Unchanged text.</p></body></html>"
    stored_page = test_critical_page.model_copy(update={"text_body": html, stored_field: [removed_url]})

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, stored_page)

    assert page_result is not None
    assert page_result.has_changes
    assert getattr(page_result, removed_field) == [HttpUrl(removed_url)]
    assert getattr(page_result, stored_field) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stored_content", "init"),
    [
        ({"text_body": None, "links": None, "documents": None}, False),
        ({"text_body": None, "links": ["https://www.test_website.com/old"], "documents": None}, False),
        (
            {
                "text_body": "<html><body><p>Old text.</p></body></html>",
                "links": ["https://www.test_website.com/old"],
                "documents": [],
            },
            True,
        ),
    ],
    ids=["new-page", "page-without-saved-text", "init-over-saved-content"],
)
async def test_check_critical_page_saves_baseline(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    stored_content: dict[str, object],
    init: bool,
):
    """Tests a page with no saved text, or any page on an init scan, has its current content saved as a
    baseline rather than reported as all new content."""
    html = (
        "<html><body><h2>Fees</h2><p>The fee is $100.</p>"
        '<a href="/apply">Apply</a><a href="/files/fees.pdf">Fees PDF</a></body></html>'
    )
    stored_page = test_critical_page.model_copy(update=stored_content)

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, stored_page, init=init)

    assert page_result is not None
    assert page_result.text_body == html
    assert page_result.links == [HttpUrl("https://www.test_website.com/apply")]
    assert page_result.documents == [HttpUrl("https://www.test_website.com/files/fees.pdf")]
    assert not page_result.has_changes
    for change_field in PAGE_CHANGE_FIELDS:
        assert not getattr(page_result, change_field), change_field


# ======================================
# Stand-in pages
# ======================================

FEES_PAGE: str = (
    "<html><body><main><h2>Fees</h2>"
    + "".join(f"<p>Application fee for permit type {number} is ${number * 10}.</p>" for number in range(12))
    + "</main></body></html>"
)
CHALLENGE_PAGE: str = (
    "<html><body><h1>Just a moment...</h1><p>Checking your browser before accessing the site.</p></body></html>"
)


@pytest.mark.anyio
async def test_a_stand_in_page_is_a_failed_check_that_keeps_the_saved_page(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a "Just a moment..." page served with 200 OK is not saved as the page's new content, which would
    report everything as removed, then everything as added once the real page is back."""
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        page_results = await check_critical_pages(client, [stored_page], init=False)

    page_result = page_results[stored_page.id]
    assert page_result.consecutive_failures == 1
    assert (page_result.last_failure_reason or "").startswith("Most of the page's content is missing")
    assert page_result.text_body is None
    assert page_result.text_removed is None


@pytest.mark.anyio
async def test_a_page_that_stays_a_stand_in_is_accepted_as_its_new_content(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a page that has lost most of its text for several scans in a row (e.g. a redesign that cut most of
    its text) is accepted as the real page, so it stops failing and its changes are reported."""
    stored_page = test_critical_page.model_copy(
        update={
            "text_body": FEES_PAGE,
            "links": [],
            "documents": [],
            "consecutive_failures": STAND_IN_FAILURES_BEFORE_ACCEPTING,
            "last_failure_reason": STAND_IN_PAGE_REASON,
        }
    )

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        page_results = await check_critical_pages(client, [stored_page], init=False)

    page_result = page_results[stored_page.id]
    assert page_result.text_body == CHALLENGE_PAGE
    assert page_result.text_removed
    assert page_result.has_changes
    assert page_result.consecutive_failures == 0
    assert page_result.last_failure_reason is None


@pytest.mark.parametrize(
    ("failures_before", "last_failure_reason", "failures_after"),
    [
        (STAND_IN_FAILURES_BEFORE_ACCEPTING - 1, STAND_IN_PAGE_REASON, STAND_IN_FAILURES_BEFORE_ACCEPTING),
        (STAND_IN_FAILURES_BEFORE_ACCEPTING, "HTTP 500", 1),  # Failing to reach the page does not count
    ],
    ids=["not-enough-scans-yet", "latest-failure-was-not-a-stand-in"],
)
@pytest.mark.anyio
async def test_a_stand_in_page_is_not_accepted_too_early(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    failures_before: int,
    last_failure_reason: str,
    failures_after: int,
):
    """Tests a stand-in page keeps failing, keeping the saved page, until it has been a stand-in for enough scans
    in a row. Scans where the page could not be reached at all are not counted."""
    stored_page = test_critical_page.model_copy(
        update={
            "text_body": FEES_PAGE,
            "links": [],
            "documents": [],
            "consecutive_failures": failures_before,
            "last_failure_reason": last_failure_reason,
        }
    )

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        page_results = await check_critical_pages(client, [stored_page], init=False)

    page_result = page_results[stored_page.id]
    assert page_result.consecutive_failures == failures_after
    assert page_result.last_failure_reason == STAND_IN_PAGE_REASON
    assert page_result.text_body is None


def _refuse_connection(request: httpx2.Request) -> httpx2.Response:
    """Fails every request as if the server could not be connected to."""
    raise httpx2.ConnectError("Connection refused", request=request)


def _drop_connection(request: httpx2.Request) -> httpx2.Response:
    """Fails every request as if the connection dropped part-way through reading the page."""
    raise httpx2.ReadError("Connection reset by peer", request=request)


def _serve_a_pdf(request: httpx2.Request) -> httpx2.Response:
    """Answers every request with a PDF file, as if the page had been replaced by a document."""
    return httpx2.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "failure_reason"),
    [
        (_refuse_connection, "Connection failed or timed out"),
        (_drop_connection, "Request failed"),
        (_serve_a_pdf, "The page is now a file (application/pdf), not a web page"),
    ],
    ids=["could-not-connect", "connection-dropped", "now-a-file"],
)
async def test_a_critical_page_with_no_response_is_a_failed_check_saying_why(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler: RequestHandler,
    failure_reason: str,
) -> None:
    """Tests a critical page whose request fails without any response is counted as a failed check, saying why it
    failed, and keeps its saved copy."""
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})

    async with mock_client_factory(handler) as client:
        page_results = await check_critical_pages(client, [stored_page], init=False)

    page_result = page_results[stored_page.id]
    assert page_result.consecutive_failures == 1
    assert page_result.last_failure_reason == failure_reason
    assert page_result.text_body is None


# ======================================
# Ignore rules and the page's own links
# ======================================


@pytest.mark.anyio
async def test_a_page_that_really_changed_is_not_mistaken_for_a_stand_in(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a page that keeps most of its text but has one edit is reported as changed, not as a stand-in."""
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})
    new_page: str = FEES_PAGE.replace("$110.", "$115.")

    async with mock_client_factory(lambda request: httpx2.Response(200, html=new_page)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, stored_page)

    assert page_result is not None
    assert page_result.text_changed is not None and len(page_result.text_changed) == 1


@pytest.mark.parametrize(("ignore_rules", "reported"), [([], True), ([r"Page last updated: .*"], False)])
@pytest.mark.anyio
async def test_ignore_rules_hide_text_that_changes_every_scan(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    ignore_rules: list[str],
    reported: bool,
):
    """Tests text matching one of the page's ignore rules is not reported when it changes, while it is reported
    without the rule."""
    old_page = "<html><body><main><p>Page last updated: 1 Oct 2026</p><p>Fee is $50.</p></main></body></html>"
    new_page = old_page.replace("1 Oct", "7 Oct")
    stored_page = test_critical_page.model_copy(
        update={"text_body": old_page, "links": [], "documents": [], "ignore_rules": ignore_rules}
    )

    async with mock_client_factory(lambda request: httpx2.Response(200, html=new_page)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, stored_page)

    assert (page_result is not None) is reported


def _page_with_menu(menu_links: str, content_links: str) -> str:
    """Builds a page with a site-wide menu and its own content, each with the given links."""
    return f"<html><body><nav>{menu_links}</nav><main><p>Fees.</p>{content_links}</main></body></html>"


@pytest.mark.anyio
async def test_a_change_to_the_site_wide_menu_is_not_reported_on_a_critical_page(
    test_critical_page: CriticalPageRead, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient]
):
    """Tests only links in a critical page's own content are watched, so a menu shared by every page does not report
    a change on every critical page, while a link added to the page's content is still reported."""
    html = _page_with_menu('<a href="/about">About</a>', '<a href="/apply">Apply</a>')

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        baseline = await check_critical_page(client, test_critical_page, init=True)
        assert baseline is not None and baseline.links == [HttpUrl("https://www.test_website.com/apply")]
        saved = test_critical_page.model_copy(update=baseline.model_dump(exclude_unset=True, exclude={"url"}))

        html = _page_with_menu('<a href="/careers">Careers</a>', '<a href="/apply">Apply</a><a href="/dates">D</a>')
        page_result = await check_critical_page(client, saved)

    assert page_result is not None
    assert page_result.links_added == [HttpUrl("https://www.test_website.com/dates")]
    assert page_result.links_removed == []


@pytest.mark.anyio
async def test_menu_links_saved_by_an_older_version_are_dropped_without_being_reported(
    test_critical_page: CriticalPageRead, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient]
):
    """Tests the first scan after only the page's content was watched does not report every menu link saved before
    then as removed, and saves just the content's links."""
    html = _page_with_menu('<a href="/about">About</a>', '<a href="/apply">Apply</a>')
    saved_by_older_version = test_critical_page.model_copy(
        update={
            "text_body": html,
            "links": [HttpUrl("https://www.test_website.com/about"), HttpUrl("https://www.test_website.com/apply")],
            "documents": [],
        }
    )

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        page_result = await check_critical_page(client, saved_by_older_version)

    assert page_result is not None
    assert not page_result.has_changes
    assert page_result.links == [HttpUrl("https://www.test_website.com/apply")]


@pytest.mark.anyio
async def test_relative_links_are_resolved_against_the_page_a_critical_page_redirects_to(
    test_critical_page: CriticalPageRead, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient]
):
    """Tests a relative link on a page that redirected is resolved against where the page now is, so it is not
    reported as a different link."""

    def redirect_to_new_section(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/test_critical_page":
            return httpx2.Response(301, headers={"Location": "https://www.test_website.com/new-section/page"})
        return httpx2.Response(200, text='<html><body><main><a href="details">Details</a></main></body></html>')

    async with mock_client_factory(redirect_to_new_section) as client:
        baseline = await check_critical_page(client, test_critical_page, init=True)

    assert baseline is not None
    assert baseline.links == [HttpUrl("https://www.test_website.com/new-section/details")]


@pytest.mark.anyio
async def test_a_document_added_to_a_critical_pages_content_is_reported_but_not_one_in_the_menu(
    test_critical_page: CriticalPageRead, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient]
) -> None:
    """Tests a document (e.g. a PDF) added to a critical page's own content is reported and saved, while one added to
    the website's menu is not."""
    stored_page = test_critical_page.model_copy(
        update={"text_body": _page_with_menu("", ""), "links": [], "documents": []}
    )
    html = _page_with_menu('<a href="/files/menu.pdf">Menu PDF</a>', '<a href="/files/fees.pdf">Fees PDF</a>')

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        page_result: CriticalPageScanResult | None = await check_critical_page(client, stored_page)

    assert page_result is not None
    assert page_result.documents_added == [HttpUrl("https://www.test_website.com/files/fees.pdf")]
    assert page_result.documents_removed == []
    assert page_result.documents == [HttpUrl("https://www.test_website.com/files/fees.pdf")]
