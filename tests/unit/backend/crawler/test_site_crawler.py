import asyncio
from collections.abc import Callable

import httpx2
import pytest

from app.backend.crawler.site_crawler import crawl_site, fetch_internal_links_from_url
from app.core.errors import TrafficError, WebConnectionError, WebsiteTooLargeError, WebsiteUnavailableError
from tests.conftest import RequestHandler

# ========================
# Test fetch_internal_links_from_url
# ========================


@pytest.mark.anyio
async def test_fetch_internal_links_from_url_success(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests that fetch_internal_links_from_url successfully retrieves content and extracts internal links."""

    async with mock_client_factory(website_handler) as client:
        url, links, status_code = await fetch_internal_links_from_url(client, test_url, asyncio.Semaphore(2))

    assert url == test_url
    assert links == [  # The link to external.com is left out
        f"{test_url}404-page.html",
        f"{test_url}about",
        f"{test_url}contact",
        f"{test_url}page1.html",
    ]
    assert status_code == 200


@pytest.mark.anyio
async def test_fetch_internal_links_from_url_traffic_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that a 429 status code correctly triggers a TrafficError exception."""
    async with mock_client_factory(rate_limit_handler) as client:
        with pytest.raises(TrafficError) as exc_info:
            await fetch_internal_links_from_url(client, test_url, asyncio.Semaphore(2))

    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_fetch_internal_links_from_url_connection_error(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    connection_error_handler: RequestHandler,
):
    """Tests that connection errors correctly raise WebConnectionError."""
    async with mock_client_factory(connection_error_handler) as client:
        with pytest.raises(WebConnectionError):
            await fetch_internal_links_from_url(client, test_url, asyncio.Semaphore(2))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler_name", "expected_status_code"),
    [("server_error_handler", 500), ("request_error_handler", None), ("unexpected_error_handler", None)],
)
async def test_fetch_internal_links_from_url_non_fatal_errors(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler_name: str,
    expected_status_code: int | None,
    request: pytest.FixtureRequest,
):
    """Tests that non-fatal errors handle gracefully, returning empty links."""
    handler: RequestHandler = request.getfixturevalue(handler_name)
    async with mock_client_factory(handler) as client:
        url, links, status_code = await fetch_internal_links_from_url(client, test_url, asyncio.Semaphore(2))

    assert url == test_url
    assert links == []
    assert status_code == expected_status_code


# ========================
# Test crawl_site
# ========================


@pytest.mark.anyio
async def test_crawl_site_success_and_skips_404(
    test_url: str,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests that the crawler successfully navigates valid pages and leaves out pages it cannot reach (404s)."""
    async with mock_client_factory(website_handler) as client:
        visited: set[str] = await crawl_site(client, test_url, max_pages=10)

    assert visited == {test_url, f"{test_url}page1.html", f"{test_url}page2.html"}
    assert f"{test_url}404-page.html" not in visited


@pytest.mark.anyio
async def test_crawl_site_refuses_a_website_with_more_pages_than_max_pages(
    test_url: str,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a website with more pages than the limit is refused, rather than partly crawled."""
    requested_urls: list[str] = []

    def counting_handler(request: httpx2.Request) -> httpx2.Response:
        response: httpx2.Response = website_handler(request)
        if response.is_success:  # Pages that cannot be reached do not count towards the limit
            requested_urls.append(str(request.url))
        return response

    async with mock_client_factory(counting_handler) as client:
        with pytest.raises(WebsiteTooLargeError) as exc_info:
            await crawl_site(client, test_url, max_pages=2)

    assert exc_info.value.max_pages == 2
    assert len(requested_urls) == 2  # The limit is never exceeded


@pytest.mark.anyio
async def test_crawl_site_allows_a_website_with_exactly_max_pages(
    test_url: str,
    website_handler: RequestHandler,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a website with exactly as many pages as the limit is crawled in full."""
    async with mock_client_factory(website_handler) as client:
        visited: set[str] = await crawl_site(client, test_url, max_pages=3)

    assert len(visited) == 3


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
@pytest.mark.parametrize("handler_name", ["server_error_handler", "request_error_handler", "unexpected_error_handler"])
async def test_crawl_site_raises_when_the_home_page_cannot_be_loaded(
    test_url: str,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    handler_name: str,
    request: pytest.FixtureRequest,
):
    """Tests a home page that cannot be loaded fails the crawl, rather than returning no pages, which would be
    reported as every page on the website having been removed."""
    handler: RequestHandler = request.getfixturevalue(handler_name)
    async with mock_client_factory(handler) as client:
        with pytest.raises(WebsiteUnavailableError):
            await crawl_site(client, test_url)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [403, 404, 500])
async def test_crawl_site_raises_when_the_home_page_returns_an_error(
    test_url: str, mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient], status_code: int
):
    """Tests a blocked, missing or broken home page fails the crawl, so a website behind a firewall is not saved with
    no pages and never really monitored."""
    async with mock_client_factory(lambda request: httpx2.Response(status_code)) as client:
        with pytest.raises(WebsiteUnavailableError):
            await crawl_site(client, test_url)


@pytest.mark.anyio
async def test_crawl_site_follows_homepage_redirect_to_www(
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a homepage redirecting from example.com to www.example.com is still crawled, with its links."""
    pages: dict[str, str] = {
        "https://www.example.com/": '<a href="/about">About</a><a href="https://example.com/contact">Contact</a>',
        "https://www.example.com/about": "<p>About us</p>",
        "https://www.example.com/contact": "<p>Contact us</p>",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if request.url.host == "example.com":
            return httpx2.Response(301, headers={"location": url.replace("://example.com", "://www.example.com")})
        if url in pages:
            return httpx2.Response(200, text=pages[url])
        return httpx2.Response(404)

    async with mock_client_factory(handler) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10)

    assert "https://www.example.com/about" in visited
    assert any(url.endswith("/contact") for url in visited)


@pytest.mark.anyio
async def test_crawl_site_visits_a_page_linked_with_and_without_www_once(
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
):
    """Tests a website that answers on both example.com and www.example.com without redirecting, and links to
    both (and with capitals in the domain), has each page visited and returned once."""
    page_paths: dict[str, str] = {
        "/": '<a href="https://www.example.com/about">About</a><a href="https://EXAMPLE.com/about/">About</a>',
        "/about": '<a href="https://example.com/">Home</a><a href="https://www.example.com/">Home</a>',
    }
    requested_urls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        if request.url.path in page_paths:
            return httpx2.Response(200, text=page_paths[request.url.path])
        return httpx2.Response(404)

    async with mock_client_factory(handler) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10)

    assert visited == {"https://example.com/", "https://example.com/about"}
    assert len(requested_urls) == 2


@pytest.mark.anyio
async def test_crawl_site_cancels_its_other_requests_when_it_gives_up(test_url: str):
    """Tests the rest of a round is cancelled when one page fails for good, so an abandoned crawl does not
    keep requesting pages in the background while the next website is scanned."""
    gave_up = asyncio.Event()
    requests_finished_after_giving_up: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == test_url:
            return httpx2.Response(
                200, text='<a href="/slow1">Slow</a><a href="/slow2">Slow</a><a href="/down">Down</a>'
            )
        if url.endswith("/down"):
            raise httpx2.ConnectError("Mocked Connection Error", request=request)
        await asyncio.sleep(0.1)
        if gave_up.is_set():
            requests_finished_after_giving_up.append(url)
        return httpx2.Response(200, text="<p>Slow page</p>")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(WebConnectionError):
            await crawl_site(client, test_url)
        gave_up.set()
        await asyncio.sleep(0.2)

    assert requests_finished_after_giving_up == []


# ========================
# Pages the crawler leaves out, and how it treats errors
# ========================


def _serve_pages(pages: dict[str, str], error_statuses: dict[str, int] | None = None) -> RequestHandler:
    """Makes a request handler for a small website on example.com.

    Args:
        pages: The HTML of each page, by its path.
        error_statuses: The error status code each other path answers with. Any other path is not found (404).

    Returns:
        The request handler.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        path: str = request.url.path
        if path in pages:
            return httpx2.Response(200, html=pages[path])
        return httpx2.Response((error_statuses or {}).get(path, 404))

    return handler


@pytest.mark.anyio
async def test_fetch_internal_links_from_url_skips_a_page_that_redirects_off_the_website() -> None:
    """Tests a page that redirects to another website is skipped, so the other website's links are not crawled."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "example.com":
            return httpx2.Response(302, headers={"Location": "https://other.com/landing"})
        return httpx2.Response(200, html='<a href="https://other.com/more">More</a>')

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        result: tuple[str, list[str], int | None] = await fetch_internal_links_from_url(
            client, "https://example.com/go", asyncio.Semaphore(1), base_url="https://example.com/"
        )

    assert result == ("https://example.com/go", [], None)


@pytest.mark.anyio
async def test_fetch_internal_links_from_url_skips_a_file() -> None:
    """Tests a link that turns out to be a file (e.g. a PDF) is skipped rather than read for links."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        result: tuple[str, list[str], int | None] = await fetch_internal_links_from_url(
            client, "https://example.com/download?id=7", asyncio.Semaphore(1)
        )

    assert result == ("https://example.com/download?id=7", [], None)


@pytest.mark.anyio
async def test_crawl_site_leaves_out_files_and_pages_that_redirect_off_the_website() -> None:
    """Tests a linked file and a link that redirects to another website are not listed as pages of the website, while
    the rest of the website is still crawled."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        match request.url.path:
            case "/":
                return httpx2.Response(
                    200, html='<a href="/about">About</a><a href="/download">Form</a><a href="/go">Partner</a>'
                )
            case "/about":
                return httpx2.Response(200, html="<p>About us</p>")
            case "/download":
                return httpx2.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})
            case "/go":
                return httpx2.Response(302, headers={"Location": "https://other.com/"})
            case _:
                return httpx2.Response(404)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10)

    assert visited == {"https://example.com/", "https://example.com/about"}


@pytest.mark.anyio
async def test_crawl_site_only_crawls_pages_under_a_website_with_a_path() -> None:
    """Tests a website added with a path (e.g. example.com/au) only has the pages under that path crawled."""
    requested_paths: list[str] = []
    serve: RequestHandler = _serve_pages(
        {
            "/au": '<a href="/au/news">News</a><a href="/us/news">US news</a><a href="/">Global</a>',
            "/au/news": "<p>News</p>",
            "/us/news": "<p>US news</p>",
            "/": "<p>Global</p>",
        }
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_paths.append(request.url.path)
        return serve(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com/au/", max_pages=10)

    assert visited == {"https://example.com/au", "https://example.com/au/news"}
    assert sorted(requested_paths) == ["/au", "/au/news"]


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [403, 404, 410, 500])
async def test_crawl_site_leaves_out_a_page_that_returns_an_error(status_code: int) -> None:
    """Tests a page that is forbidden, missing or broken is left out of the website's pages, while the rest of the
    website is still crawled."""
    handler: RequestHandler = _serve_pages(
        {"/": '<a href="/broken">Broken</a><a href="/about">About</a>', "/about": "<p>About</p>"},
        error_statuses={"/broken": status_code},
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10)

    assert visited == {"https://example.com/", "https://example.com/about"}


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
async def test_crawl_site_raises_when_a_page_keeps_rate_limiting(status_code: int) -> None:
    """Tests a page that keeps answering with a rate-limiting or busy-server status stops the crawl with a
    TrafficError, so the website can be slowed down or put on cooldown instead of being saved with pages missing."""
    handler: RequestHandler = _serve_pages(
        {"/": '<a href="/busy">Busy</a><a href="/about">About</a>', "/about": "<p>About</p>"},
        error_statuses={"/busy": status_code},
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(TrafficError) as exc_info:
            await crawl_site(client, "https://example.com", max_pages=10)

    assert exc_info.value.status_code == status_code


@pytest.mark.anyio
async def test_crawl_site_keeps_a_page_that_was_only_busy_for_a_moment() -> None:
    """Tests a page that is busy (503) once and then loads is retried and kept, rather than left out."""
    busy_answers_left: list[int] = [1]
    serve: RequestHandler = _serve_pages({"/": '<a href="/busy">Busy</a>', "/busy": "<p>Loaded at last</p>"})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/busy" and busy_answers_left:
            busy_answers_left.pop()
            return httpx2.Response(503)
        return serve(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10)

    assert visited == {"https://example.com/", "https://example.com/busy"}


@pytest.mark.anyio
async def test_crawl_site_raises_when_a_batch_meets_the_403_threshold() -> None:
    """Tests a round of pages where as many as the threshold are forbidden (403) is treated as the website blocking
    the crawler, rather than as those pages having been removed."""
    page_paths: list[str] = ["/one", "/two", "/three"]
    handler: RequestHandler = _serve_pages(
        {"/": "".join(f'<a href="{path}">Page</a>' for path in page_paths)},
        error_statuses=dict.fromkeys(page_paths, 403),
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(TrafficError) as exc_info:
            await crawl_site(client, "https://example.com", max_pages=10, batch_403_threshold=len(page_paths))

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_crawl_site_skips_forbidden_pages_below_the_403_threshold() -> None:
    """Tests a round with fewer forbidden (403) pages than the threshold only leaves those pages out."""
    page_paths: list[str] = ["/one", "/two", "/three"]
    handler: RequestHandler = _serve_pages(
        {"/": "".join(f'<a href="{path}">Page</a>' for path in page_paths)},
        error_statuses=dict.fromkeys(page_paths, 403),
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=10, batch_403_threshold=4)

    assert visited == {"https://example.com/"}


@pytest.mark.anyio
async def test_crawl_site_is_not_too_large_when_the_page_left_was_already_reached_by_a_redirect() -> None:
    """Tests a website with exactly the page limit is not refused as too large when the only link left to visit
    goes to a page already visited by following a redirect from another link."""
    # Links are visited in alphabetical order, so "/moved" is visited (and redirected to "/new") before "/new"
    serve: RequestHandler = _serve_pages({"/": '<a href="/moved">Old</a><a href="/new">New</a>', "/new": "<p>New</p>"})

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/moved":
            return httpx2.Response(301, headers={"Location": "https://example.com/new"})
        return serve(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        visited: set[str] = await crawl_site(client, "https://example.com", max_pages=2)

    assert visited == {"https://example.com/", "https://example.com/new"}
