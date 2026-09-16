from collections.abc import Callable

import httpx2
import pytest

from app.backend.engine import get_critical_page_updates, get_website_updates
from app.core.errors import TrafficError
from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
from app.models.website_models import WebsiteRead
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
        updates = await get_critical_page_updates(client, initial_page)

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
        initial_updates = await get_critical_page_updates(client, test_critical_page)

        # 2. Pre-populate our base model with that target state
        synced_page = test_critical_page.model_copy(
            update={
                "text_body": initial_updates.text_body,
                "links": initial_updates.links,
                "documents": initial_updates.documents,
            }
        )

        # 3. Re-run against synced state; zero diffs should be detected
        second_updates = await get_critical_page_updates(client, synced_page)

    assert second_updates.url == test_critical_page.url

    # No diffs detected
    assert not second_updates.recent_links_added
    assert not second_updates.recent_links_removed
    assert not second_updates.recent_text_changed
    assert not second_updates.recent_documents_added
    assert not second_updates.recent_documents_removed

    # Base payload fields stay None to avoid redundant DB writes
    assert second_updates.links is None
    assert second_updates.documents is None
    assert second_updates.text_body is None


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
        updates = await get_website_updates(
            client=client, stored_website=initial_website, max_pages=10, delay=0, concurrent=2
        )

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
