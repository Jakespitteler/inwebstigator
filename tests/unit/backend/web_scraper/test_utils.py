from collections.abc import Callable

import httpx2
import pytest

from app.backend.web_scraper.utils import extract_links, fetch_content_from_url
from tests.conftest import RequestHandler

# =============================
# Test fetch_content_from_url
# =============================


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
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    redirect_handler: RequestHandler,
) -> None:
    """Test that the function correctly follows redirects and returns the final destination URL."""

    async with mock_client_factory(redirect_handler) as client:
        content, final_url = await fetch_content_from_url(client, url="https://example.com/initial")

    assert final_url == "https://example.com/final"
    assert content == "Final Destination Content"


# =============================
# Test extract_links
# =============================


def test_extract_links_basic_and_relative(test_url: str) -> None:
    """Test standard absolute, relative links, alphabetical sorting, and uniqueness."""
    html_content: str = """
    <html>
        <body>
            <a href="https://example.com/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/contact">Contact Duplicate</a>
            <a href="https://example.com/services">Services</a>
        </body>
    </html>
    """
    result: list[str] = extract_links(test_url, html_content, internal_only=False)

    expected: list[str] = [
        "https://example.com/about",
        "https://example.com/contact",
        "https://example.com/services",
    ]
    assert result == expected


def test_extract_links_internal_only(test_url: str) -> None:
    """Test filtering for internal links only when internal_only=True."""
    html_content: str = """
    <html>
        <body>
            <a href="/internal-page">Internal Relative</a>
            <a href="https://example.com/another-internal">Internal Absolute</a>
            <a href="https://external.com/page">External Link</a>
            <a href="/policy/policy.pdf">PDF Document</a>
        </body>
    </html>
    """
    result: list[str] = extract_links(test_url, html_content, internal_only=True)

    expected: list[str] = [
        "https://example.com/another-internal",
        "https://example.com/internal-page",
    ]
    assert result == expected


def test_extract_links_skips_non_navigational(test_url: str) -> None:
    """Test that empty strings, hash anchors, javascript, mailto, and tel schemes are ignored."""
    html_content: str = """
    <html>
        <body>
            <a href="">Empty</a>
            <a href="#">Hash Only</a>
            <a href="javascript:void(0);">JS Action</a>
            <a href="mailto:test@example.com">Email</a>
            <a href="tel:+123456789">Phone</a>
            <a href="/valid-page">Valid Page</a>
        </body>
    </html>
    """
    result: list[str] = extract_links(test_url, html_content)

    expected: list[str] = ["https://example.com/valid-page"]
    assert result == expected


def test_extract_links_fragment_stripping(test_url: str) -> None:
    """Test that URL fragments/anchors are stripped and duplicates are consolidated."""
    html_content: str = """
    <html>
        <body>
            <a href="/page#section1">Section 1</a>
            <a href="/page#section2">Section 2</a>
            <a href="/page">Base Page</a>
        </body>
    </html>
    """
    result: list[str] = extract_links(test_url, html_content)

    # All variations should collapse to the same clean URL and de-duplicate
    expected: list[str] = ["https://example.com/page"]
    assert result == expected


def test_extract_links_invalid_url(test_url: str) -> None:
    """Test that ValueError is raised when url lacks a valid netloc/domain."""
    invalid_url: str = "not-a-valid-url"
    html_content: str = '<a href="/page">Page</a>'

    with pytest.raises(ValueError, match="Invalid url provided"):
        extract_links(invalid_url, html_content)


def test_extract_links_skips_missing_href(test_url: str) -> None:
    """Test that anchor tags without an href attribute are gracefully ignored."""
    html_content: str = """
    <html>
        <body>
            <a>No Href</a>
            <a name="anchor">Named Anchor</a>
            <a href="/valid">Valid</a>
        </body>
    </html>
    """
    result: list[str] = extract_links(test_url, html_content)

    expected: list[str] = ["https://example.com/valid"]
    assert result == expected
