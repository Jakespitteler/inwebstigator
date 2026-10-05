import asyncio
import logging
from collections.abc import Awaitable, Iterable, Iterator

import httpx2

from app.backend.utils.http_client import fetch_content_from_url
from app.backend.utils.links import (
    extract_links_from_html,
    is_internal_web_page,
    normalise_url,
)
from app.core.errors import TrafficError, WebConnectionError, WebsiteTooLargeError

logger = logging.getLogger(__name__)


async def fetch_internal_links_from_url(
    client: httpx2.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    delay: float | None = None,
    base_url: str | None = None,
) -> tuple[str, list[str], int | None]:
    """Safely fetches HTML content and extracts internal links under concurrency constraints.

    Uses an asyncio Semaphore to throttle concurrent requests. Connection failures and rate-limiting
    responses are retried by `fetch_content_from_url`. Ensures redirects remain within the
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
                logger.warning(f"Skipping {url=} as it redirected outside the website to {absolute_url}.")
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
            logger.warning(f"Skipping {url=} due to page-level HTTP error ({status_code}).")
            return url, [], status_code

        except (TrafficError, WebConnectionError):
            raise  # Already retried by `fetch_content_from_url`

        except httpx2.RequestError as e:
            logger.warning(f"Skipping {url=} due to general request error. Raised: {e}")
            return url, [], None

        except Exception as e:  # Breaks on a bunch of "Cannot send a request, as the client has been closed."
            logger.warning(f"Skipping {url=} due to unexpected error. Raised: {e}")
            return url, [], None


async def _gather_or_cancel[T](awaitables: Iterable[Awaitable[T]]) -> list[T]:
    """Runs awaitables concurrently and returns their results in order, like `asyncio.gather()`.

    Unlike `asyncio.gather()`, if one fails the others are cancelled before the error is raised.
    Otherwise they would keep requesting pages in the background from a website the crawl has
    given up on (even one that has rate limited us), while the next website is being scanned.

    Args:
        awaitables: The awaitables to run concurrently.

    Returns:
        The result of each awaitable, in the order given.
    """
    tasks: list[asyncio.Future[T]] = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)  # Wait until they have all stopped
        raise


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

    A website with more pages than the limit is refused rather than partly crawled, as comparing
    a different partial crawl each scan would report pages being added and removed that never were.

    Args:
        client: The HTTP client instance used to execute network requests.
        url: The entry point URL string from which the crawler discovers links.
        max_pages: The most unique internal pages the crawler will visit. Defaults to 5000.
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
        WebsiteTooLargeError: If the limit is reached while there are still pages left to visit.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    url = normalise_url(url)
    visited: set[str] = set()
    queued: set[str] = {url}
    queue: list[str] = [url]

    while queue and len(visited) < max_pages:
        logger.info(f"{url}: Queue size: {len(queue)} | Visited: {len(visited)}")

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
        batch_results: list[tuple[str, list[str], int | None]] = await _gather_or_cancel(tasks)

        batch_403_count = 0
        for visited_url, internal_links, status_code in batch_results:
            if status_code == 403:
                batch_403_count += 1

            if status_code != 200:
                continue

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
    # The crawl only stops with pages left to visit when it has reached the limit. Queued links that
    # were already reached by following a redirect from another link are not counted as left to visit.
    if any(link not in visited for link in queue):
        raise WebsiteTooLargeError(url, max_pages)

    logger.info(f"Crawl completed. Exhausted all discoverable links. Total visited: {len(visited)}")
    return visited
