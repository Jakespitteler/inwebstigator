from httpx2 import AsyncClient, ConnectError, HTTPStatusError, Response, TimeoutException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError


@retry(
    wait=wait_exponential(
        multiplier=config.fetch_site_retry_multiplier,
        min=config.fetch_site_retry_min_wait_seconds,
        max=config.fetch_site_retry_max_wait_seconds,
    ),
    stop=stop_after_attempt(config.fetch_site_retry_max_attempts),
    retry=retry_if_exception_type((WebConnectionError, TrafficError)),
    reraise=True,
)
async def fetch_content_from_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Fetches raw HTML content from a given URL asynchronously. Follows redirects to the url that is returned is

    Args:
        client (httpx2.AsyncClient): The HTTPX2 asynchronous client instance used for the request.
        url (str): The target URL to fetch content from.

    Returns:
        tuple[str, str]: The raw HTML text content and the final URL (after any redirects).

    Timeouts, connection failures and rate limiting are retried with exponential backoff.

    Raises:
        TrafficError: If the server keeps returning a rate-limiting status code (429, 502, 503, 504).
        WebConnectionError: If the request keeps timing out or failing to connect.
        httpx2.HTTPStatusError: If the HTTP response returns any other unsuccessful status code (4xx or 5xx).
        httpx2.RequestError: If any other request failure occurs.

    """
    try:
        response: Response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
    except HTTPStatusError as e:
        if e.response.status_code in {429, 502, 503, 504}:
            raise TrafficError(url, e.response.status_code) from e
        raise
    except (TimeoutException, ConnectError) as e:
        raise WebConnectionError(url) from e
    return response.text, str(response.url)
