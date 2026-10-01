import asyncio
import uuid
from collections.abc import Callable

import httpx2
import pytest
from pytest_mock import MockerFixture

from app.backend.engine import get_critical_page_updates, get_website_updates
from app.core.errors import TrafficError
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.website_models import WebsiteRead, WebsiteUpdate
from tests.conftest import RequestHandler

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
    """Tests that idempotency prevents useless DB updates by syncing the model first."""
    async with mock_client_factory(website_handler) as client:
        # 1. Run once to extract the parsed target state from the mock HTML
        initial_updates: CriticalPageUpdate | None = await get_critical_page_updates(client, test_critical_page)

        # 2. Pre-populate our base model with that target state
        assert initial_updates
        synced_page = test_critical_page.model_copy(
            update={
                "text_body": initial_updates.text_body,
                "links": initial_updates.links,
                "documents": initial_updates.documents,
            }
        )

        # 3. Re-run against synced state; zero diffs should be detected
        second_updates: CriticalPageUpdate | None = await get_critical_page_updates(client, synced_page)
    assert not second_updates


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
    assert getattr(updates, recent_removed_field) == [removed_url]
    assert getattr(updates, stored_field) == []


@pytest.mark.anyio
async def test_get_critical_page_updates_saves_baseline_for_new_page(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a page fetched for the first time is saved as a baseline, not reported as all new content."""
    html = (
        "<html><body><h2>Fees</h2><p>The fee is $100.</p>"
        '<a href="/apply">Apply</a><a href="/files/fees.pdf">Fees PDF</a></body></html>'
    )
    new_page = test_critical_page.model_copy(update={"text_body": None, "links": None, "documents": None})

    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, new_page)

    assert updates is not None
    assert updates.text_body == html
    assert updates.links == ["https://www.test_website.com/apply"]
    assert updates.documents == ["https://www.test_website.com/files/fees.pdf"]
    for recent_field in (
        "recent_links_added",
        "recent_links_removed",
        "recent_documents_added",
        "recent_documents_removed",
        "recent_text_added",
        "recent_text_removed",
        "recent_text_changed",
    ):
        assert getattr(updates, recent_field) == [], recent_field


@pytest.mark.anyio
async def test_get_critical_page_updates_page_with_saved_links_is_not_a_baseline(
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a page that has been fetched before (it has saved links) still reports changes."""
    stored_page = test_critical_page.model_copy(
        update={"text_body": None, "links": ["https://www.test_website.com/old"], "documents": None}
    )

    html = '<html><body><a href="/new">New</a></body></html>'
    async with mock_client_factory(lambda request: httpx2.Response(200, text=html)) as client:
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, stored_page)

    assert updates is not None
    assert updates.recent_links_added == ["https://www.test_website.com/new"]
    assert updates.recent_links_removed == ["https://www.test_website.com/old"]


# ======================================
# get_website_updates
# ======================================


@pytest.mark.anyio
async def test_get_website_updates_with_changes(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,  # 1. Inject critical page fixture
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests the orchestrator loop catches internal map changes and delegates critical page updates."""
    initial_website = test_website.model_copy(
        update={
            "internal_links": [test_internal_link],
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
            client=client, stored_website=initial_website, max_pages=10, delay=0, concurrent=2
        )
    assert updates
    assert updates.url == initial_website.url

    # Crawler internal link diff assertions
    assert updates.recent_added_internal_links is not None
    assert any("page1.html" in link for link in updates.recent_added_internal_links)

    # Since `test_internal_link` wasn't crawled by website_handler, it gets correctly flagged as removed
    assert updates.recent_removed_internal_links is not None
    assert len(updates.recent_removed_internal_links) == len(initial_website.internal_links)
    assert initial_website.internal_links[0].url in updates.recent_removed_internal_links

    # Delegated Critical Page updates assertion
    assert updates.critical_page_updates is not None
    assert len(initial_website.critical_pages) > 0

    cp_id = initial_website.critical_pages[0].id
    assert cp_id in updates.critical_page_updates

    cp_update = updates.critical_page_updates[cp_id]
    assert cp_update.recent_links_added is not None
    assert len(cp_update.recent_links_added) > 0
    assert cp_update.recent_links_removed == [f"{test_website.url}old-link"]
    assert cp_update.recent_text_changed is not None


@pytest.mark.anyio
async def test_get_website_updates_traffic_error(
    test_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that fatal connection/traffic errors cleanly bubble up from the orchestrator."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(TrafficError) as exc_info:
            await get_website_updates(client=client, stored_website=test_website, max_pages=10, delay=0, concurrent=2)

    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_get_website_updates_skips_a_broken_critical_page(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    mocker: MockerFixture,
):
    """Tests one critical page failing (e.g. deleted, now 404) does not stop the other pages and crawl."""
    mocker.patch("app.backend.engine.crawl_site", return_value={test_website.url, f"{test_website.url}new-page"})
    working_page = test_critical_page.model_copy(update={"text_body": "<html><body><p>Old text.</p></body></html>"})
    broken_page = test_critical_page.model_copy(
        update={"id": uuid.uuid4(), "url": f"{test_website.url}deleted-page", "text_body": "<p>Was here.</p>"}
    )
    website = test_website.model_copy(update={"critical_pages": [broken_page, working_page], "internal_links": []})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == broken_page.url:
            return httpx2.Response(404)
        return httpx2.Response(200, text="<html><body><p>New text.</p></body></html>")

    async with mock_client_factory(handler) as client:
        updates: WebsiteUpdate | None = await get_website_updates(client, website, None, None, None)

    assert updates is not None
    assert updates.critical_page_updates is not None
    assert set(updates.critical_page_updates) == {working_page.id}
    assert updates.critical_page_updates[working_page.id].recent_text_changed
    assert updates.recent_added_internal_links  # the crawl still ran


@pytest.mark.anyio
async def test_get_website_updates_does_not_swallow_cancellation(
    test_website: WebsiteRead,
    test_critical_page: CriticalPageRead,
    mocker: MockerFixture,
):
    """Tests cancellation (e.g. the app shutting down) still stops the scan rather than being skipped."""
    mocker.patch("app.backend.engine.get_critical_page_updates", side_effect=asyncio.CancelledError())
    crawl = mocker.patch("app.backend.engine.crawl_site")
    website = test_website.model_copy(update={"critical_pages": [test_critical_page]})

    with pytest.raises(asyncio.CancelledError):
        await get_website_updates(mocker.Mock(), website, None, None, None)
    crawl.assert_not_called()
