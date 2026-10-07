from collections.abc import Callable

import httpx2
import pytest

from app.backend.page_fetcher import fetch_content_from_url
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError
from tests.conftest import RequestHandler


@pytest.mark.anyio
async def test_fetch_content_from_url_success(
    test_url: str,
    test_html_content: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
) -> None:
    """Test successful HTML content retrieval."""
    async with mock_client_factory(website_handler) as client:
        content, url = await fetch_content_from_url(client, url=test_url)
    assert url == test_url
    assert content == test_html_content


@pytest.mark.anyio
async def test_fetch_content_from_url_http_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    server_error_handler: RequestHandler,
) -> None:
    """Test that HTTP status errors raise correctly through the mock pipeline."""
    async with mock_client_factory(server_error_handler) as client:
        with pytest.raises(httpx2.HTTPStatusError):
            await fetch_content_from_url(client, url=test_url)


@pytest.mark.anyio
async def test_fetch_content_from_url_request_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    connection_error_handler: RequestHandler,
) -> None:
    """Test that network connection errors raise WebConnectionError correctly."""
    async with mock_client_factory(connection_error_handler) as client:
        with pytest.raises(WebConnectionError):
            await fetch_content_from_url(client, url=test_url)


@pytest.mark.anyio
async def test_fetch_content_from_url_retries_rate_limit(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
) -> None:
    """Test that a rate limit is retried before TrafficError is raised."""
    requests: list[httpx2.Request] = []

    def counting_handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return rate_limit_handler(request)

    async with mock_client_factory(counting_handler) as client:
        with pytest.raises(TrafficError):
            await fetch_content_from_url(client, url=test_url)

    assert len(requests) == config.fetch_site_retry_max_attempts


@pytest.mark.anyio
async def test_fetch_content_from_url_redirects(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    redirect_handler: RequestHandler,
) -> None:
    """Test that the function correctly follows redirects and returns the final destination URL."""

    async with mock_client_factory(redirect_handler) as client:
        content, final_url = await fetch_content_from_url(client, url=f"{test_url}initial")

    assert final_url == f"{test_url}final"
    assert content == "Final Destination Content"
