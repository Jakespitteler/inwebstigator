import asyncio
import logging
from collections.abc import Awaitable, Iterator

import httpx2
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.backend.utils.http_client import fetch_content_from_url
from app.backend.utils.links import (
    extract_links_from_html,
    is_internal_web_page,
    normalise_url,
)
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError

logger = logging.getLogger(__name__)


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
async def fetch_internal_links_from_url(
    client: httpx2.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    delay: float | None = None,
    base_url: str | None = None,
) -> tuple[str, list[str], int | None]:
    """Safely fetches HTML content and extracts internal links under concurrency constraints.

    Uses an asyncio Semaphore to throttle concurrent requests and applies retry logic
    for connection failures or rate-limiting responses. Ensures redirects remain within the
    target base domain scope.

    Args:
        client: The HTTP client instance used to execute network requests.
        url: The target URL string to fetch and parse for internal links.
        semaphore: An asyncio Semaphore controlling maximum concurrent HTTP operations.
        delay: Optional duration in seconds to wait before executing the HTTP request.
        base_url: Optional base domain URL string used to validate internal links.
            Defaults to `url` if omitted.

    Returns:
        A tuple containing three elements:
            - The final resolved URL string after following redirects.
            - A list of extracted internal URL strings found on the page.
            - The HTTP response status code (or None if an unhandled non-HTTP error occurred).

    Raises:
        TrafficError: If the server returns a rate-limiting or throttling status code (429, 502, 503, 504).
        WebConnectionError: If a request timeout or network connection failure occurs.
    """
    if not base_url:
        base_url = url
    async with semaphore:
        try:
            if delay:
                await asyncio.sleep(delay)
            logger.debug(f"Fetching {url=}...")
            html_content: str
            html_content, absolute_url = await fetch_content_from_url(client, url)

            # Ensure redirect was not to an external site
            if not is_internal_web_page(base_url, check_url=absolute_url):
                return url, [], None

            links: list[str] = extract_links_from_html(
                base_url=base_url,
                url=absolute_url,
                html_content=html_content,
                internal_only=True,
            )

            logger.debug(f"Successfully extracted {len(links)} internal links from {url=}")
            return absolute_url, links, 200

        except httpx2.HTTPStatusError as e:
            status_code = e.response.status_code

            if status_code in {429, 502, 503, 504}:
                raise TrafficError(url, status_code) from e
            logger.warning(f"Skipping {url=} due to page-level HTTP error ({status_code}).")
            return url, [], status_code

        except (httpx2.TimeoutException, httpx2.ConnectError) as e:
            raise WebConnectionError(url) from e

        except httpx2.RequestError as e:
            logger.warning(f"Skipping {url=} due to general request error. Raised: {e}")
            return url, [], None

        except Exception as e:  # Breaks on a bunch of "Cannot send a request, as the client has been closed."
            logger.warning(f"Skipping {url=} due to unexpected error. Raised: {e}")
            return url, [], None


async def crawl_site(
    client: httpx2.AsyncClient,
    url: str,
    max_pages: int = 5000,
    max_concurrent: int = 10,
    delay: float = 0,
    batch_403_threshold: int = 20,
) -> set[str]:
    """Asynchronously crawls a website starting from an entry URL up to a maximum page limit.

    Traverses internal links in batched concurrent async requests, tracking visited and queued
    URLs. Monitors batch HTTP responses to detect site-wide blockades or firewall restrictions.

    Args:
        client: The HTTP client instance used to execute network requests.
        url: The entry point URL string from which the crawler discovers links.
        max_pages: The maximum number of unique internal pages to visit before stopping.
            Defaults to 5000.
        max_concurrent: The maximum number of concurrent HTTP requests permitted.
            Defaults to 10.
        delay: The time in seconds to pause before fetching individual URLs. Defaults to 0.
        batch_403_threshold: The threshold count of 403 Forbidden responses in a single batch
            that triggers a site-wide block exception. Defaults to 20.

    Returns:
        A set of normalized internal URL strings visited during the crawl.

    Raises:
        TrafficError: If a single batch encounters 403 Forbidden responses equal to or exceeding
            batch_403_threshold, indicating firewall blocking or access denial.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    url = normalise_url(url)
    visited: set[str] = set()
    queued: set[str] = {url}
    queue: list[str] = [url]

    while queue and len(visited) < max_pages:
        logger.info(f"Queue size: {len(queue)} | Visited: {len(visited)}")

        batch_size: int = min(len(queue), max_pages - len(visited))
        batch: list[str] = queue[:batch_size]
        queue = queue[batch_size:]

        tasks: Iterator[Awaitable[tuple[str, list[str], int | None]]] = (
            fetch_internal_links_from_url(
                client=client,
                base_url=url,
                url=current_url,
                semaphore=semaphore,
                delay=delay,
            )
            for current_url in batch
        )
        batch_results: list[tuple[str, list[str], int | None]] = await asyncio.gather(*tasks)

        batch_403_count = 0
        for visited_url, internal_links, status_code in batch_results:
            if status_code == 403:
                batch_403_count += 1

            visited.add(normalise_url(visited_url))
            for link in internal_links:
                if link not in visited and link not in queued:
                    queued.add(link)
                    queue.append(link)

        if batch_403_count >= batch_403_threshold:
            logger.error(
                f"Site-wide block detected: Encountered {batch_403_count} 403 Forbidden responses in a single batch."
            )
            raise TrafficError(url, 403)

    if len(visited) >= max_pages:
        logger.warning(f"Crawler exceeded the {max_pages=}, stopping crawler...")
    else:
        logger.info(f"Crawl completed. Exhausted all discoverable links. Total visited: {len(visited)}")

    return visited
