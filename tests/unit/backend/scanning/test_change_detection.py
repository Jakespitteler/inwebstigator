import asyncio
import uuid
from collections.abc import Callable
from contextlib import nullcontext

import httpx2
import pytest
from pydantic import HttpUrl, ValidationError
from pytest_mock import MockerFixture

from app.backend.scanning.change_detection import (
    DEFAULT_MAX_PAGES,
    STAND_IN_FAILURES_BEFORE_ACCEPTING,
    STAND_IN_PAGE_REASON,
    CrawlFailedError,
    get_critical_page_only_updates,
    get_critical_page_updates,
    get_website_updates,
)
from app.core.config import config
from app.core.errors import MostPagesMissingError, TrafficError
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.website_models import WebsiteRead, WebsiteUpdate
from tests.conftest import RequestHandler

RECENT_PAGE_FIELDS = (
    "recent_links_added",
    "recent_links_removed",
    "recent_documents_added",
    "recent_documents_removed",
    "recent_text_added",
    "recent_text_removed",
    "recent_text_changed",
)

# ======================================
# get_critical_page_updates
# ======================================


@pytest.mark.anyio
async def test_get_critical_page_updates_with_changes(
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
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, initial_page)
    assert updates
    assert updates.has_changes
    assert updates.url == initial_page.url
    assert updates.recent_links_added == [
        HttpUrl("https://www.test_website.com/about"),
        HttpUrl("https://www.test_website.com/new-link"),
    ]
    assert updates.recent_links_removed == [HttpUrl(f"{test_critical_page.url}old-link")]
    assert [block.text for block in updates.recent_text_removed or []] == ["Old Content"]
    assert updates.text_body is not None and "Updated Critical Page Content" in updates.text_body


@pytest.mark.anyio
async def test_get_critical_page_updates_no_changes(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests a page re-scanned with the same content as its saved baseline returns no updates."""
    new_page = test_critical_page.model_copy(update={"text_body": None, "links": None, "documents": None})

    async with mock_client_factory(website_handler) as client:
        baseline: CriticalPageUpdate | None = await get_critical_page_updates(client, new_page)
        assert baseline is not None

        synced_page = new_page.model_copy(
            update={"text_body": baseline.text_body, "links": baseline.links, "documents": baseline.documents}
        )
        assert await get_critical_page_updates(client, synced_page) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stored_field", "recent_removed_field", "removed_url"),
    [
        ("documents", "recent_documents_removed", "https://www.test_website.com/files/policy.pdf"),
        ("links", "recent_links_removed", "https://www.test_website.com/old-page"),
    ],
    ids=["last-document", "last-link"],
)
async def test_get_critical_page_updates_detects_last_link_or_document_removed(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    stored_field: str,
    recent_removed_field: str,
    removed_url: str,
):
    """Tests removing a page's only link or document is reported and saved, not ignored as "no change"."""
    html = "<html><body><p>Unchanged text.</p></body></html>"
    stored_page = test_critical_page.model_copy(update={"text_body": html, stored_field: [removed_url]})

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page)

    assert updates is not None
    assert updates.has_changes
    assert getattr(updates, recent_removed_field) == [HttpUrl(removed_url)]
    assert getattr(updates, stored_field) == []


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
async def test_get_critical_page_updates_saves_baseline(
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
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page, init=init)

    assert updates is not None
    assert updates.text_body == html
    assert updates.links == [HttpUrl("https://www.test_website.com/apply")]
    assert updates.documents == [HttpUrl("https://www.test_website.com/files/fees.pdf")]
    assert not updates.has_changes
    for recent_field in RECENT_PAGE_FIELDS:
        assert not getattr(updates, recent_field), recent_field


# ======================================
# get_critical_page_only_updates
# ======================================


@pytest.mark.anyio
async def test_get_critical_page_only_updates_checks_critical_pages_without_crawling(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    mocker: MockerFixture,
):
    """Tests only the critical pages are checked, so a website too large to crawl can still be watched."""
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site")
    website = test_website.model_copy(update={"critical_pages": [test_critical_page]})

    async with mock_client_factory(website_handler) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    crawl.assert_not_called()
    assert updates is not None
    assert updates.critical_page_updates is not None
    assert updates.critical_page_updates[test_critical_page.id].text_body is not None  # The page's baseline
    assert updates.initial_internal_links is None
    assert not updates.recent_added_internal_links
    assert not updates.recent_removed_internal_links


@pytest.mark.anyio
async def test_get_critical_page_only_updates_returns_none_when_nothing_changed(
    test_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a check that finds nothing to save returns None, so nothing is written."""
    website = test_website.model_copy(update={"critical_pages": []})

    assert await get_critical_page_only_updates(mocker.Mock(), website) is None


# ======================================
# get_website_updates
# ======================================


@pytest.mark.anyio
async def test_get_website_updates_with_changes(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests the orchestrator loop catches internal map changes and delegates critical page updates."""
    initial_website = test_website.model_copy(
        update={
            "critical_pages": [
                test_critical_page.model_copy(
                    update={
                        "text_body": "<html><body><p>Old Content</p></body></html>",
                        "links": [f"{test_website.url}old-link"],
                    }
                )
            ],
        }
    )

    async with mock_client_factory(website_handler) as client:
        updates: WebsiteUpdate | None = await get_website_updates(
            client=client,
            stored_website=initial_website,
            stored_internal_links=[test_internal_link.url],
            max_pages=10,
            delay=0,
            concurrent=2,
        )
    assert updates
    assert updates.has_changes
    assert updates.url == initial_website.url
    assert updates.initial_internal_links is None

    # Crawler internal link diff assertions
    assert updates.recent_added_internal_links is not None
    assert any("page1.html" in str(link) for link in updates.recent_added_internal_links)

    # Since `test_internal_link` wasn't crawled by website_handler, it gets correctly flagged as removed
    assert updates.recent_removed_internal_links is not None
    assert updates.recent_removed_internal_links == [test_internal_link.url]

    # Delegated Critical Page updates assertion
    assert updates.critical_page_updates is not None
    cp_id = initial_website.critical_pages[0].id
    assert cp_id in updates.critical_page_updates
    assert updates.changed_page_ids == {cp_id}

    cp_update = updates.critical_page_updates[cp_id]
    assert cp_update.recent_links_added is not None
    assert len(cp_update.recent_links_added) > 0
    assert cp_update.recent_links_removed == [HttpUrl(f"{test_website.url}old-link")]
    assert [block.text for block in cp_update.recent_text_removed or []] == ["Old Content"]


@pytest.mark.anyio
async def test_get_website_updates_returns_none_when_nothing_changed(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    mocker: MockerFixture,
):
    """Tests a scan that finds no changes and has no baselines to save returns None, so nothing is written."""
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={test_internal_link.url})
    website = test_website.model_copy(update={"critical_pages": []})

    assert await get_website_updates(mocker.Mock(), website, [test_internal_link.url], None, None, None) is None


@pytest.mark.anyio
@pytest.mark.parametrize("init", [False, True], ids=["no-saved-links", "init-over-saved-links"])
async def test_get_website_updates_saves_internal_link_baseline(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    mocker: MockerFixture,
    init: bool,
):
    """Tests a website with no saved internal links (e.g. its first scan failed), or any website on an init
    scan, saves its crawled links as a baseline rather than reporting them all as added."""
    crawled = {str(test_website.url), f"{test_website.url}about"}
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value=crawled)
    website = test_website.model_copy(update={"critical_pages": []})
    stored_internal_links = [test_internal_link.url] if init else []

    updates: WebsiteUpdate | None = await get_website_updates(
        mocker.Mock(), website, stored_internal_links, None, None, None, init=init
    )

    assert updates is not None
    assert {str(link) for link in updates.initial_internal_links or []} == crawled
    assert not updates.recent_added_internal_links
    assert not updates.recent_removed_internal_links
    assert not updates.has_changes


@pytest.mark.anyio
async def test_get_website_updates_returns_new_page_baseline_without_changes(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    mocker: MockerFixture,
):
    """Tests a newly added critical page is returned so its baseline is saved, without counting as a change."""
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={test_internal_link.url})
    new_page = test_critical_page.model_copy(update={"text_body": None, "links": None, "documents": None})
    website = test_website.model_copy(update={"critical_pages": [new_page]})
    html = "<html><body><p>New page.</p></body></html>"

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        updates: WebsiteUpdate | None = await get_website_updates(
            client, website, [test_internal_link.url], None, None, None
        )

    assert updates is not None
    assert updates.critical_page_updates is not None
    assert updates.critical_page_updates[new_page.id].text_body == html
    assert updates.changed_page_ids == set()
    assert not updates.has_changes


@pytest.mark.anyio
async def test_get_website_updates_traffic_error(
    test_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that fatal connection/traffic errors cleanly bubble up from the orchestrator, carrying what the critical
    pages' check found."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(CrawlFailedError) as exc_info:
            await get_website_updates(
                client=client,
                stored_website=test_website,
                stored_internal_links=[],
                max_pages=10,
                delay=0,
                concurrent=2,
            )

    assert isinstance(exc_info.value.error, TrafficError)
    assert exc_info.value.error.status_code == 429


@pytest.mark.anyio
async def test_get_website_updates_records_a_broken_critical_page(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    mocker: MockerFixture,
):
    """Tests one critical page failing (e.g. deleted, now 404) is recorded without stopping the other pages."""
    mocker.patch(
        "app.backend.scanning.change_detection.crawl_site",
        return_value={test_website.url, f"{test_website.url}new-page"},
    )
    working_page = test_critical_page.model_copy(update={"text_body": "<html><body><p>Old text.</p></body></html>"})
    broken_page = test_critical_page.model_copy(
        update={"id": uuid.uuid4(), "url": f"{test_website.url}deleted-page", "text_body": "<p>Was here.</p>"}
    )
    website = test_website.model_copy(update={"critical_pages": [broken_page, working_page]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == broken_page.url:
            return httpx2.Response(404)
        return httpx2.Response(200, text="<html><body><p>New text.</p></body></html>")

    async with mock_client_factory(handler) as client:
        updates: WebsiteUpdate | None = await get_website_updates(client, website, [], None, None, None)

    assert updates is not None
    assert updates.critical_page_updates is not None
    assert set(updates.critical_page_updates) == {working_page.id, broken_page.id}
    assert updates.critical_page_updates[working_page.id].recent_text_changed
    assert updates.critical_page_updates[broken_page.id].last_failure_reason == "HTTP 404"
    assert updates.initial_internal_links  # the crawl still ran


@pytest.mark.anyio
async def test_critical_page_bug_is_not_counted_as_the_page_being_unreachable(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mocker: MockerFixture,
):
    """Tests an unexpected error (not a failed fetch) is skipped, rather than counted as a failure."""
    mocker.patch(
        "app.backend.scanning.change_detection.get_critical_page_updates", side_effect=ValueError("Parsing bug")
    )
    website = test_website.model_copy(update={"critical_pages": [test_critical_page]})

    assert await get_critical_page_only_updates(mocker.Mock(), website) is None


@pytest.mark.anyio
async def test_get_website_updates_stops_on_cancellation(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mocker: MockerFixture,
):
    """Tests cancellation (e.g. the app shutting down) stops the scan before the crawl, rather than the page
    being recorded as a failed check."""
    mocker.patch(
        "app.backend.scanning.change_detection.get_critical_page_updates", side_effect=asyncio.CancelledError()
    )
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site")
    website = test_website.model_copy(update={"critical_pages": [test_critical_page]})

    with pytest.raises(asyncio.CancelledError):
        await get_website_updates(mocker.Mock(), website, [], None, None, None)
    crawl.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "failures_before", "failures_after", "reported"),
    [
        (500, 0, 1, False),  # A single failure may be a blip
        (500, 1, 2, True),  # Reaching the failure limit reports the page
        (500, 2, 3, False),  # A page that stays down is not reported again
        (404, 0, 2, True),  # A missing page is reported straight away
        (410, 0, 2, True),  # So is a page that is gone
        (503, 0, 1, False),  # A page that keeps rate limiting the crawler is a failed check, not a cooldown
    ],
)
async def test_failed_critical_page_check_is_counted_and_reported_once(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    status_code: int,
    failures_before: int,
    failures_after: int,
    reported: bool,
):
    """Tests each failed check adds to the page's failure count, and the page is only reported once."""
    stored_page = test_critical_page.model_copy(update={"consecutive_failures": failures_before})
    website = test_website.model_copy(update={"critical_pages": [stored_page]})

    async with mock_client_factory(lambda request: httpx2.Response(status_code)) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    assert updates is not None
    assert updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.consecutive_failures == failures_after
    assert page_update.last_failure_reason == f"HTTP {status_code}"
    assert page_update.text_body is None  # The last good copy is kept
    assert updates.has_changes is reported


# ======================================
# WebsiteUpdate change detection
# ======================================

CHANGED_PAGE_ID = uuid.uuid4()
BASELINE_PAGE_ID = uuid.uuid4()


@pytest.mark.parametrize(
    ("updates", "changed_page_ids", "has_changes"),
    [
        (WebsiteUpdate(), set[uuid.UUID](), False),
        (WebsiteUpdate(initial_internal_links=[HttpUrl("https://www.test_website.com/")]), set[uuid.UUID](), False),
        (WebsiteUpdate(recent_added_internal_links=[], recent_removed_internal_links=[]), set[uuid.UUID](), False),
        (
            WebsiteUpdate(recent_removed_internal_links=[HttpUrl("https://www.test_website.com/old")]),
            set[uuid.UUID](),
            True,
        ),
        (
            WebsiteUpdate(
                critical_page_updates={
                    CHANGED_PAGE_ID: CriticalPageUpdate(
                        url=HttpUrl("https://www.test_website.com/fees"),
                        recent_links_added=[HttpUrl("https://www.test_website.com/new")],
                    ),
                    BASELINE_PAGE_ID: CriticalPageUpdate(
                        url=HttpUrl("https://www.test_website.com/new-page"),
                        text_body="<p>Hi</p>",
                        links=[],
                        documents=[],
                    ),
                }
            ),
            {CHANGED_PAGE_ID},
            True,
        ),
    ],
    ids=["empty", "internal-link-baseline", "empty-link-diff", "link-removed", "changed-and-baseline-pages"],
)
def test_website_update_change_detection(updates: WebsiteUpdate, changed_page_ids: set[uuid.UUID], has_changes: bool):
    """Tests only real changes count as changes, and pages that only saved a baseline are not reported."""
    assert updates.changed_page_ids == changed_page_ids
    assert updates.has_changes is has_changes


# ======================================
# Stand-in pages and ignore rules
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
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a "Just a moment..." page served with 200 OK is not saved as the page's new content, which would
    report everything as removed, then everything as added once the real page is back."""
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})
    website = test_website.model_copy(update={"critical_pages": [stored_page]})

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    assert updates is not None and updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.consecutive_failures == 1
    assert (page_update.last_failure_reason or "").startswith("Most of the page's content is missing")
    assert page_update.text_body is None
    assert page_update.recent_text_removed is None


@pytest.mark.anyio
async def test_a_page_that_stays_a_stand_in_is_accepted_as_its_new_content(
    test_website: WebsiteRead,
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
    website = test_website.model_copy(update={"critical_pages": [stored_page]})

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    assert updates is not None and updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.text_body == CHALLENGE_PAGE
    assert page_update.recent_text_removed
    assert page_update.has_changes
    assert page_update.consecutive_failures == 0
    assert page_update.last_failure_reason is None


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
    test_website: WebsiteRead,
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
    website = test_website.model_copy(update={"critical_pages": [stored_page]})

    async with mock_client_factory(lambda request: httpx2.Response(200, html=CHALLENGE_PAGE)) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    assert updates is not None and updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.consecutive_failures == failures_after
    assert page_update.last_failure_reason == STAND_IN_PAGE_REASON
    assert page_update.text_body is None


@pytest.mark.anyio
async def test_a_page_that_really_changed_is_not_mistaken_for_a_stand_in(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})
    """Tests a page that keeps most of its text but has one edit is reported as changed, not as a stand-in."""
    new_page: str = FEES_PAGE.replace("$110.", "$115.")

    async with mock_client_factory(lambda request: httpx2.Response(200, html=new_page)) as client:
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page)

    assert updates is not None
    assert updates.recent_text_changed is not None and len(updates.recent_text_changed) == 1


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
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page)

    assert (updates is not None) is reported


def test_a_link_that_is_not_a_url_cannot_be_set_on_a_critical_page_update() -> None:
    """Tests a value set on an update after it is made is checked, so a bad link is refused when it is found rather
    than saved and then failing every later scan of the website when it is read back."""
    update = CriticalPageUpdate(url=HttpUrl("https://example.com/"))

    with pytest.raises(ValidationError):
        update.recent_links_added = ["sms:+61400000000"]  # pyright: ignore[reportAttributeAccessIssue]


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
        baseline = await get_critical_page_updates(client, test_critical_page, init=True)
        assert baseline is not None and baseline.links == [HttpUrl("https://www.test_website.com/apply")]
        saved = test_critical_page.model_copy(update=baseline.model_dump(exclude_unset=True, exclude={"url"}))

        html = _page_with_menu('<a href="/careers">Careers</a>', '<a href="/apply">Apply</a><a href="/dates">D</a>')
        updates = await get_critical_page_updates(client, saved)

    assert updates is not None
    assert updates.recent_links_added == [HttpUrl("https://www.test_website.com/dates")]
    assert updates.recent_links_removed == []


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
        updates = await get_critical_page_updates(client, saved_by_older_version)

    assert updates is not None
    assert not updates.has_changes
    assert updates.links == [HttpUrl("https://www.test_website.com/apply")]


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
        baseline = await get_critical_page_updates(client, test_critical_page, init=True)

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
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page)

    assert updates is not None
    assert updates.recent_documents_added == [HttpUrl("https://www.test_website.com/files/fees.pdf")]
    assert updates.recent_documents_removed == []
    assert updates.documents == [HttpUrl("https://www.test_website.com/files/fees.pdf")]


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
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler: RequestHandler,
    failure_reason: str,
) -> None:
    """Tests a critical page whose request fails without any response is counted as a failed check, saying why it
    failed, and keeps its saved copy."""
    stored_page = test_critical_page.model_copy(update={"text_body": FEES_PAGE, "links": [], "documents": []})
    website = test_website.model_copy(update={"critical_pages": [stored_page]})

    async with mock_client_factory(handler) as client:
        updates: WebsiteUpdate | None = await get_critical_page_only_updates(client, website)

    assert updates is not None and updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.consecutive_failures == 1
    assert page_update.last_failure_reason == failure_reason
    assert page_update.text_body is None


KNOWN_PAGE_COUNT: int = config.web_crawler_min_known_pages_to_check_missing
MOST_MISSING_ALLOWED: int = int(KNOWN_PAGE_COUNT * config.web_crawler_max_missing_pages_ratio)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("missing_count", "accept_missing_pages", "is_partly_down"),
    [
        (MOST_MISSING_ALLOWED, False, False),
        (MOST_MISSING_ALLOWED + 1, False, True),
        (MOST_MISSING_ALLOWED + 1, True, False),
    ],
    ids=["allowed-share-missing", "most-pages-missing", "most-pages-missing-accepted"],
)
async def test_a_crawl_missing_most_known_pages_is_treated_as_the_website_being_partly_down(
    test_website: WebsiteRead,
    mocker: MockerFixture,
    missing_count: int,
    accept_missing_pages: bool,
    is_partly_down: bool,
) -> None:
    """Tests a crawl that cannot find more than the allowed share of a website's known pages fails with
    MostPagesMissingError, so they are not saved as removed, unless missing pages are being accepted. Missing no more
    than the allowed share reports the pages as removed."""
    known_pages = [HttpUrl(f"{test_website.url}page-{number}") for number in range(KNOWN_PAGE_COUNT)]
    crawled = {str(url) for url in known_pages[missing_count:]}
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value=crawled)
    website = test_website.model_copy(update={"critical_pages": []})

    with pytest.raises(CrawlFailedError) if is_partly_down else nullcontext() as failure:
        updates: WebsiteUpdate | None = await get_website_updates(
            mocker.Mock(), website, known_pages, None, None, None, accept_missing_pages=accept_missing_pages
        )
        assert updates is not None
        assert set(updates.recent_removed_internal_links or []) == set(known_pages[:missing_count])

    if failure is not None:
        assert isinstance(failure.value.error, MostPagesMissingError)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("max_pages", "delay", "concurrent", "crawl_settings"),
    [
        (None, None, None, {"max_pages": DEFAULT_MAX_PAGES, "delay": 1.5, "max_concurrent": 4}),
        (10, 2.5, 3, {"max_pages": 10, "delay": 2.5, "max_concurrent": 3}),
    ],
    ids=["website-own-settings", "settings-given-for-this-scan"],
)
async def test_get_website_updates_crawls_with_the_websites_own_settings_unless_given_others(
    test_website: WebsiteRead,
    mocker: MockerFixture,
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
    crawl_settings: dict[str, float],
) -> None:
    """Tests the website is crawled with its own delay and concurrency, and the default page limit, unless the scan
    is given its own."""
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value=set[str]())
    website = test_website.model_copy(
        update={"critical_pages": [], "recommended_delay": 1.5, "recommended_concurrent": 4}
    )

    await get_website_updates(mocker.Mock(), website, [], max_pages, delay, concurrent)

    assert {setting: crawl.call_args.kwargs[setting] for setting in crawl_settings} == crawl_settings
