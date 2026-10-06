import asyncio
import uuid
from collections.abc import Callable

import httpx2
import pytest
from pytest_mock import MockerFixture

from app.backend.engine import get_critical_page_only_updates, get_critical_page_updates, get_website_updates
from app.core.errors import TrafficError
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
    assert updates.recent_links_added is not None
    assert updates.recent_links_removed == [f"{test_critical_page.url}old-link"]
    assert updates.recent_text_changed is not None


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
    assert getattr(updates, recent_removed_field) == [removed_url]
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
    assert updates.links == ["https://www.test_website.com/apply"]
    assert updates.documents == ["https://www.test_website.com/files/fees.pdf"]
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
    crawl = mocker.patch("app.backend.engine.crawl_site")
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
    assert any("page1.html" in link for link in updates.recent_added_internal_links)

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
    assert cp_update.recent_links_removed == [f"{test_website.url}old-link"]
    assert cp_update.recent_text_changed is not None


@pytest.mark.anyio
async def test_get_website_updates_returns_none_when_nothing_changed(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    mocker: MockerFixture,
):
    """Tests a scan that finds no changes and has no baselines to save returns None, so nothing is written."""
    mocker.patch("app.backend.engine.crawl_site", return_value={test_internal_link.url})
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
    crawled = {test_website.url, f"{test_website.url}about"}
    mocker.patch("app.backend.engine.crawl_site", return_value=crawled)
    website = test_website.model_copy(update={"critical_pages": []})
    stored_internal_links = [test_internal_link.url] if init else []

    updates: WebsiteUpdate | None = await get_website_updates(
        mocker.Mock(), website, stored_internal_links, None, None, None, init=init
    )

    assert updates is not None
    assert set(updates.initial_internal_links or []) == crawled
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
    mocker.patch("app.backend.engine.crawl_site", return_value={test_internal_link.url})
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
    """Tests that fatal connection/traffic errors cleanly bubble up from the orchestrator."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(TrafficError) as exc_info:
            await get_website_updates(
                client=client,
                stored_website=test_website,
                stored_internal_links=[],
                max_pages=10,
                delay=0,
                concurrent=2,
            )

    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_get_website_updates_records_a_broken_critical_page(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    mocker: MockerFixture,
):
    """Tests one critical page failing (e.g. deleted, now 404) is recorded without stopping the other pages."""
    mocker.patch("app.backend.engine.crawl_site", return_value={test_website.url, f"{test_website.url}new-page"})
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
    mocker.patch("app.backend.engine.get_critical_page_updates", side_effect=ValueError("Parsing bug"))
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
    mocker.patch("app.backend.engine.get_critical_page_updates", side_effect=asyncio.CancelledError())
    crawl = mocker.patch("app.backend.engine.crawl_site")
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
        (WebsiteUpdate(initial_internal_links=["https://www.test_website.com/"]), set[uuid.UUID](), False),
        (WebsiteUpdate(recent_added_internal_links=[], recent_removed_internal_links=[]), set[uuid.UUID](), False),
        (WebsiteUpdate(recent_removed_internal_links=["https://www.test_website.com/old"]), set[uuid.UUID](), True),
        (
            WebsiteUpdate(
                critical_page_updates={
                    CHANGED_PAGE_ID: CriticalPageUpdate(
                        url="https://www.test_website.com/fees",
                        recent_links_added=["https://www.test_website.com/new"],
                    ),
                    BASELINE_PAGE_ID: CriticalPageUpdate(
                        url="https://www.test_website.com/new-page", text_body="<p>Hi</p>", links=[], documents=[]
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
