from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest
from tenacity import RetryCallState, Retrying

from app.backend.crawler.page_fetcher import (
    fetch_content_from_url,
    new_http_client,
    retry_after_seconds,
    wait_for_server,
)
from app.core.config import config
from app.core.errors import NotAWebPageError, TrafficError, WebConnectionError
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


@pytest.mark.anyio
async def test_new_http_client_names_the_app_in_its_user_agent() -> None:
    """Tests requests say they come from Inwebstigator, rather than sending the HTTP library's own User-Agent, which
    many firewalls block."""
    async with new_http_client() as client:
        assert client.headers["User-Agent"] == config.web_crawler_user_agent
        assert "Inwebstigator" in client.headers["User-Agent"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", 120.0),
        ("Wed, 07 Oct 2026 10:02:00 GMT", 120.0),
        ("Wed, 07 Oct 2026 09:59:00 GMT", 0.0),
        ("Wed, 07 Oct 2026 10:02:00 -0000", 120.0),
        ("soon", None),
        (None, None),
    ],
    ids=["seconds", "date", "date-in-the-past", "date-without-a-time-zone", "unreadable", "missing"],
)
def test_retry_after_seconds_reads_both_forms_of_the_header(header: str | None, expected: float | None) -> None:
    """Tests a server's Retry-After header is read whether it gives seconds or a date."""
    headers: dict[str, str] = {"Retry-After": header} if header else {}
    response = httpx2.Response(429, headers=headers)

    assert retry_after_seconds(response, now=datetime(2026, 10, 7, 10, 0, tzinfo=UTC)) == expected


@pytest.mark.parametrize(
    ("retry_after", "expected_wait"),
    [(None, 2.0), (10.0, 10.0), (3600.0, 60.0)],
    ids=["no-header-uses-backoff", "asked-for-longer", "capped"],
)
def test_waits_as_long_as_the_server_asks_up_to_a_limit(retry_after: float | None, expected_wait: float) -> None:
    """Tests a rate-limited page is retried after the time the server asked for, but never holds up a scan for
    hours (the website is put on cooldown instead if it keeps refusing)."""
    retry_state = RetryCallState(retry_object=Retrying(), fn=None, args=(), kwargs={})
    retry_state.attempt_number = 1
    retry_state.set_exception(
        (TrafficError, TrafficError("https://example.com", 429, retry_after_seconds=retry_after), None)
    )

    assert wait_for_server(retry_state) == expected_wait


@pytest.mark.anyio
async def test_a_file_is_not_read_as_a_web_page() -> None:
    """Tests a link that turns out to be a file (e.g. a PDF without ".pdf" in its URL) is not read as a page."""

    def serve_pdf(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(serve_pdf)) as client:
        with pytest.raises(NotAWebPageError):
            await fetch_content_from_url(client, "https://example.com/download?id=7")


def _counting_handler(response_for: Callable[[int], httpx2.Response], requests: list[httpx2.Request]) -> RequestHandler:
    """Makes a request handler that records each request and answers it with the response for that try.

    Args:
        response_for: Gives the response for each try, numbered from 1.
        requests: The list each request is added to.

    Returns:
        The request handler.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return response_for(len(requests))

    return handler


def _retry_state_after(error: BaseException) -> RetryCallState:
    """Makes the retry state after a first try that failed with the given error.

    Args:
        error: The error the first try raised.

    Returns:
        The retry state.
    """
    retry_state = RetryCallState(retry_object=Retrying(), fn=None, args=(), kwargs={})
    retry_state.attempt_number = 1
    retry_state.set_exception((type(error), error, None))
    return retry_state


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
async def test_a_busy_server_is_retried_then_raises_a_traffic_error(status_code: int) -> None:
    """Tests a rate-limiting or busy-server status is tried again, up to the limit, before a TrafficError with that
    status is raised."""
    requests: list[httpx2.Request] = []
    handler: RequestHandler = _counting_handler(lambda _: httpx2.Response(status_code), requests)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(TrafficError) as exc_info:
            await fetch_content_from_url(client, "https://example.com/")

    assert exc_info.value.status_code == status_code
    assert len(requests) == config.fetch_site_retry_max_attempts


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [403, 404, 500])
async def test_other_error_statuses_are_raised_without_retrying(status_code: int) -> None:
    """Tests a forbidden, missing or broken page is not tried again, as it would fail the same way every time."""
    requests: list[httpx2.Request] = []
    handler: RequestHandler = _counting_handler(lambda _: httpx2.Response(status_code), requests)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(httpx2.HTTPStatusError) as exc_info:
            await fetch_content_from_url(client, "https://example.com/")

    assert exc_info.value.response.status_code == status_code
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "network_error",
    [httpx2.ConnectError("Mocked Connection Error"), httpx2.ReadTimeout("Mocked Timeout")],
    ids=["connection-failure", "timeout"],
)
async def test_connection_failures_and_timeouts_are_retried_then_raise(network_error: httpx2.TransportError) -> None:
    """Tests a page that keeps failing to connect or timing out is tried again, up to the limit, before a
    WebConnectionError is raised."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise network_error

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(WebConnectionError):
            await fetch_content_from_url(client, "https://example.com/")

    assert len(requests) == config.fetch_site_retry_max_attempts


@pytest.mark.anyio
async def test_a_page_that_loads_after_a_busy_answer_is_returned() -> None:
    """Tests a page that is busy (503) on the first try and loads on the next is returned, rather than failing."""
    requests: list[httpx2.Request] = []
    handler: RequestHandler = _counting_handler(
        lambda attempt: httpx2.Response(503) if attempt == 1 else httpx2.Response(200, html="<p>Loaded</p>"), requests
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        content, final_url = await fetch_content_from_url(client, "https://example.com/news")

    assert (content, final_url) == ("<p>Loaded</p>", "https://example.com/news")
    assert len(requests) == 2


@pytest.mark.anyio
async def test_a_traffic_error_carries_how_long_the_server_asked_to_wait() -> None:
    """Tests the Retry-After a rate-limiting server sends is kept on the TrafficError, so the wait can follow it."""
    handler: RequestHandler = _counting_handler(lambda _: httpx2.Response(429, headers={"Retry-After": "30"}), [])

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(TrafficError) as exc_info:
            await fetch_content_from_url(client, "https://example.com/")

    assert exc_info.value.retry_after_seconds == 30.0


@pytest.mark.anyio
async def test_a_rate_limited_page_is_retried_after_the_time_the_server_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests fetching a page really waits as long as the server's Retry-After asks before trying again."""
    waits: list[float] = []

    async def record_wait(seconds: float) -> None:
        waits.append(float(seconds))

    retrying = fetch_content_from_url.retry  # pyright: ignore[reportFunctionMemberAccess]
    monkeypatch.setattr(retrying, "wait", wait_for_server)
    monkeypatch.setattr(retrying, "sleep", record_wait)
    handler: RequestHandler = _counting_handler(
        lambda attempt: (
            httpx2.Response(429, headers={"Retry-After": "10"})
            if attempt == 1
            else httpx2.Response(200, html="<p>Hi</p>")
        ),
        [],
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        content, _ = await fetch_content_from_url(client, "https://example.com/")

    assert content == "<p>Hi</p>"
    assert waits == [10.0]


@pytest.mark.parametrize(
    "error",
    [
        TrafficError("https://example.com", 429, retry_after_seconds=0.5),
        WebConnectionError("https://example.com"),
    ],
    ids=["asked-for-less-than-the-backoff", "connection-failure"],
)
def test_the_usual_backoff_is_used_unless_the_server_asks_for_longer(error: BaseException) -> None:
    """Tests a server asking for a shorter wait than the usual backoff, or a failed connection, waits the usual
    backoff."""
    assert wait_for_server(_retry_state_after(error)) == float(config.fetch_site_retry_min_wait_seconds)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/html; charset=utf-8"},
        {"Content-Type": "application/xhtml+xml"},
        {"Content-Type": "TEXT/PLAIN"},
        {},
    ],
    ids=["html", "xhtml", "plain-text", "no-content-type"],
)
async def test_pages_are_read_whatever_web_page_type_they_say_they_are(headers: dict[str, str]) -> None:
    """Tests HTML, XHTML and plain text are read as web pages, and so is a response that does not say what it is."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"<p>Page</p>", headers=headers)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        content, _ = await fetch_content_from_url(client, "https://example.com/")

    assert content == "<p>Page</p>"


@pytest.mark.anyio
async def test_a_file_is_not_retried() -> None:
    """Tests a link that turns out to be an image is given up on after one request, as it would never be a page."""
    requests: list[httpx2.Request] = []
    handler: RequestHandler = _counting_handler(
        lambda _: httpx2.Response(200, content=b"\x89PNG", headers={"Content-Type": "image/png"}), requests
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(NotAWebPageError) as exc_info:
            await fetch_content_from_url(client, "https://example.com/logo")

    assert exc_info.value.content_type == "image/png"
    assert len(requests) == 1
