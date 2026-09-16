from collections.abc import Callable

import httpx2
import pytest

from app.backend.utils.http_client import fetch_content_from_url
from app.db.utils.field_types import URLString
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
    """Test that network connection errors raise RequestError correctly."""
    async with mock_client_factory(connection_error_handler) as client:
        with pytest.raises(httpx2.RequestError):
            await fetch_content_from_url(client, url=test_url)


@pytest.mark.anyio
async def test_fetch_content_from_url_redirects(
    test_url: URLString,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    redirect_handler: RequestHandler,
) -> None:
    """Test that the function correctly follows redirects and returns the final destination URL."""

    async with mock_client_factory(redirect_handler) as client:
        content, final_url = await fetch_content_from_url(client, url=f"{test_url}initial")

    assert final_url == f"{test_url}final"
    assert content == "Final Destination Content"
