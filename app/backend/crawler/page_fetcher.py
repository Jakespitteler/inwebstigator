from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from httpx2 import AsyncClient, ConnectError, HTTPStatusError, Response, TimeoutException
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import config
from app.core.errors import NotAWebPageError, TrafficError, WebConnectionError

WEB_PAGE_CONTENT_TYPES: tuple[str, ...] = ("text/html", "application/xhtml+xml", "text/plain")
TRAFFIC_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})

wait_with_backoff = wait_exponential(
    multiplier=config.fetch_site_retry_multiplier,
    min=config.fetch_site_retry_min_wait_seconds,
    max=config.fetch_site_retry_max_wait_seconds,
)


def new_http_client() -> AsyncClient:
    """Makes the HTTP client that loads websites' pages.

    It names the app in its User-Agent rather than sending the HTTP library's own, which many firewalls block.

    Returns:
        The client, to be used with `async with`.
    """
    return AsyncClient(headers={"User-Agent": config.web_crawler_user_agent})


def retry_after_seconds(response: Response, now: datetime | None = None) -> float | None:
    """Reads how long a server asked to be left before the next request, from its Retry-After header.

    Args:
        response: The server's response, e.g. a 429 or 503.
        now: The current time, for a header that gives a date. Defaults to now.

    Returns:
        The number of seconds to wait (never negative), or None if the server did not say or the header is unreadable.
    """
    header: str | None = response.headers.get("Retry-After")
    if header is None:
        return None
    if header.strip().isdigit():
        return float(header.strip())
    try:
        retry_at: datetime = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    # A date in an HTTP header is always UTC, but one ending "-0000" is read without a time zone
    retry_at_utc: datetime = retry_at if retry_at.tzinfo else retry_at.replace(tzinfo=UTC)
    return max((retry_at_utc - (now or datetime.now(UTC))).total_seconds(), 0.0)


def wait_for_server(retry_state: RetryCallState) -> float:
    """Decides how long to wait before trying a page again: the usual growing backoff, or longer if the server asked
    for longer (with Retry-After).

    The wait is capped, so a server asking for hours does not hold up the scan. If it keeps refusing, the website is
    put on cooldown instead.

    Args:
        retry_state: The state of the retries so far, including the last error.

    Returns:
        The number of seconds to wait.
    """
    backoff: float = wait_with_backoff(retry_state)
    error: BaseException | None = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(error, TrafficError) and error.retry_after_seconds is not None:
        return min(max(backoff, error.retry_after_seconds), config.fetch_site_max_retry_after_seconds)
    return backoff


def _is_web_page(response: Response) -> bool:
    """Checks whether a response is a web page, rather than a file such as a PDF or an image.

    A response that does not say what it is is treated as a web page.

    Args:
        response: The response.

    Returns:
        True if the response is HTML (or plain text).
    """
    content_type: str = response.headers.get("Content-Type", "").lower()
    return not content_type or content_type.startswith(WEB_PAGE_CONTENT_TYPES)


@retry(
    wait=wait_for_server,
    stop=stop_after_attempt(config.fetch_site_retry_max_attempts),
    retry=retry_if_exception_type((WebConnectionError, TrafficError)),
    reraise=True,
)
async def fetch_content_from_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Fetches raw HTML content from a given URL asynchronously, following redirects.

    Args:
        client (httpx2.AsyncClient): The HTTPX2 asynchronous client instance used for the request.
        url (str): The target URL to fetch content from.

    Returns:
        tuple[str, str]: The raw HTML text content and the final URL (after any redirects).

    Timeouts, connection failures and rate limiting are retried with exponential backoff, waiting longer if the
    server asks to with a Retry-After header.

    Raises:
        TrafficError: If the server keeps returning a rate-limiting status code (429, 502, 503, 504).
        WebConnectionError: If the request keeps timing out or failing to connect.
        NotAWebPageError: If the URL is a file (e.g. a PDF or an image) rather than a web page, so it is not read.
        httpx2.HTTPStatusError: If the HTTP response returns any other unsuccessful status code (4xx or 5xx).
        httpx2.RequestError: If any other request failure occurs.

    """
    try:
        response: Response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
    except HTTPStatusError as e:
        if e.response.status_code in TRAFFIC_STATUS_CODES:
            raise TrafficError(url, e.response.status_code, retry_after_seconds=retry_after_seconds(e.response)) from e
        raise
    except (TimeoutException, ConnectError) as e:
        raise WebConnectionError(url) from e
    if not _is_web_page(response):
        raise NotAWebPageError(url, response.headers.get("Content-Type", ""))
    return response.text, str(response.url)
