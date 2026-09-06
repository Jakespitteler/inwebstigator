import asyncio
from collections.abc import Callable

import httpx2
import pytest

from app.backend.web_scraper.errors import TrafficError, WebConnectionError
from app.backend.web_scraper.site_crawler import crawl_site, fetch_and_extract
from tests.conftest import RequestHandler

# ========================
# Test fetch_and_extract
# ========================


@pytest.mark.anyio
async def test_fetch_and_extract_success(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests that fetch_and_extract successfully retrieves content and extracts internal links."""

    async with mock_client_factory(website_handler) as client:
        url, links, status_code = await fetch_and_extract(client, test_url, asyncio.Semaphore(2))

    assert url == test_url
    assert len(links) == 4
    assert any("page1.html" in link for link in links)
    assert status_code == 200


@pytest.mark.anyio
async def test_fetch_and_extract_traffic_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that a 429 status code correctly triggers a TrafficError exception."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(TrafficError) as exc_info:
            await fetch_and_extract(client, test_url, asyncio.Semaphore(2))

    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_fetch_and_extract_connection_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    connection_error_handler: RequestHandler,
):
    """Tests that connection errors correctly raise WebConnectionError."""
    async with mock_client_factory(connection_error_handler) as client:
        with pytest.raises(WebConnectionError):
            await fetch_and_extract(client, test_url, asyncio.Semaphore(2))


@pytest.mark.anyio
@pytest.mark.parametrize("handler", ["server_error_handler", "request_error_handler", "unexpected_error_handler"])
async def test_fetch_and_extract_non_fatal_errors(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler: RequestHandler,
):
    """Tests that non-fatal errors handle gracefully, returning empty links."""
    async with mock_client_factory(handler) as client:
        url, links, _ = await fetch_and_extract(client, test_url, asyncio.Semaphore(2))

    assert url == test_url
    assert links == []


# ========================
# Test crawl_site
# ========================


@pytest.mark.anyio
async def test_crawl_site_success_and_skips_404(
    test_url: str,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests that the crawler successfully navigates valid pages and gracefully skips 404s."""
    async with mock_client_factory(website_handler) as client:
        visited: set[str] = await crawl_site(client, test_url, max_pages=10)

    assert len(visited) == 6
    assert test_url in visited
    assert f"{test_url}page1.html" in visited
    assert f"{test_url}page2.html" in visited
    assert f"{test_url}404-page.html" in visited


@pytest.mark.anyio
async def test_crawl_site_respects_max_pages(
    test_url: str,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests that the crawler stops exactly at the max_pages limit."""
    async with mock_client_factory(website_handler) as client:
        visited: set[str] = await crawl_site(client, test_url, max_pages=2)

    assert len(visited) == 2


@pytest.mark.anyio
async def test_crawl_site_respects_delay(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    timed_handler: tuple[RequestHandler, list[float]],
):
    """Tests that passing a delay correctly spaces out HTTP requests."""
    handler, timestamps = timed_handler

    delay_time: float = 0.05  # 50ms
    async with mock_client_factory(handler) as client:
        visited: set[str] = await crawl_site(client, test_url, delay=delay_time, max_pages=2)

    assert len(visited) == 2
    assert timestamps[1] - timestamps[0] >= delay_time


@pytest.mark.anyio
async def test_crawl_site_raises_on_rate_limit(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that encountering a 429 correctly raises the exception upstream."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(TrafficError) as exc_info:
            await crawl_site(client, test_url)

    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_crawl_site_raises_on_timeout(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    timeout_handler: RequestHandler,
):
    """Tests that encountering a timeout correctly raises the exception upstream."""
    async with mock_client_factory(timeout_handler) as client:
        with pytest.raises(WebConnectionError):
            await crawl_site(client, test_url)


@pytest.mark.anyio
async def test_crawl_site_raises_on_connect_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    connection_error_handler: RequestHandler,
):
    """Tests that dropping the connection correctly raises the exception upstream."""
    async with mock_client_factory(connection_error_handler) as client:
        with pytest.raises(WebConnectionError):
            await crawl_site(client, test_url)


@pytest.mark.anyio
@pytest.mark.parametrize("handler", ["server_error_handler", "request_error_handler", "unexpected_error_handler"])
async def test_crawl_site_non_fatal_errors(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler: RequestHandler,
):
    """Tests that non-fatal errors handle gracefully, returning empty links."""
    async with mock_client_factory(handler) as client:
        visited: set[str] = await crawl_site(client, test_url)

    assert len(visited) == 1
    assert test_url in visited
