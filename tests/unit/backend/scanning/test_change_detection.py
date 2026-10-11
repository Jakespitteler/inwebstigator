import asyncio
import uuid
from collections.abc import Callable
from contextlib import nullcontext

import httpx2
import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture

from app.backend.scanning.change_detection import (
    DEFAULT_MAX_PAGES,
    CrawlFailedError,
    get_critical_page_only_updates,
    get_website_updates,
)
from app.core.config import config
from app.core.errors import MostPagesMissingError, TrafficError
from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
from app.models.scan_result_models import WebsiteScanResult
from app.models.website_models import WebsiteRead
from tests.conftest import RequestHandler

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
        updates: WebsiteScanResult | None = await get_critical_page_only_updates(client, website)

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
        updates: WebsiteScanResult | None = await get_website_updates(
            client=client,
            stored_website=initial_website,
            stored_internal_links=[test_internal_link.url],
            max_pages=10,
            delay=0,
            concurrent=2,
        )
    assert updates
    assert updates.has_changes
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
async def test_a_page_saved_with_https_and_crawled_with_http_is_not_reported_as_changed(
    test_website: WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests a page crawled at its http address, after an earlier scan saved it at its https address (or the other
    way round), is the same page, so it is reported as neither removed nor added."""
    saved_pages = [HttpUrl("https://example.com/"), HttpUrl("http://example.com/about")]
    mocker.patch(
        "app.backend.scanning.change_detection.crawl_site",
        return_value={"http://example.com/", "https://example.com/about"},
    )
    website = test_website.model_copy(update={"critical_pages": []})

    assert await get_website_updates(mocker.Mock(), website, saved_pages, None, None, None) is None


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

    updates: WebsiteScanResult | None = await get_website_updates(
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
        updates: WebsiteScanResult | None = await get_website_updates(
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
        updates: WebsiteScanResult | None = await get_website_updates(client, website, [], None, None, None)

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
        "app.backend.scanning.critical_page_checks.get_critical_page_updates", side_effect=ValueError("Parsing bug")
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
        "app.backend.scanning.critical_page_checks.get_critical_page_updates", side_effect=asyncio.CancelledError()
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
        updates: WebsiteScanResult | None = await get_critical_page_only_updates(client, website)

    assert updates is not None
    assert updates.critical_page_updates is not None
    page_update = updates.critical_page_updates[stored_page.id]
    assert page_update.consecutive_failures == failures_after
    assert page_update.last_failure_reason == f"HTTP {status_code}"
    assert page_update.text_body is None  # The last good copy is kept
    assert updates.has_changes is reported


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
        updates: WebsiteScanResult | None = await get_website_updates(
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
