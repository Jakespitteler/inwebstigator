import asyncio
import logging
from collections.abc import Awaitable, Iterable, Iterator

import httpx2

from app.backend.crawler.links import extract_links_from_html, is_internal_web_page
from app.backend.crawler.page_fetcher import fetch_content_from_url
from app.core.errors import (
    NotAWebPageError,
    TrafficError,
    WebConnectionError,
    WebsiteTooLargeError,
    WebsiteUnavailableError,
)
from app.core.urls import normalise_url, page_key

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

            # Reading the page is slow for a big page, so it is done in a thread, keeping the dashboard responsive
            links: list[str] = await asyncio.to_thread(
                extract_links_from_html,
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

        except NotAWebPageError:
            logger.info(f"Skipping {url=} as it is a file rather than a web page.")
            return url, [], None

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


def _pages_left_to_visit(queue: list[str], visited: dict[str, str]) -> bool:
    """The crawl only stops with pages left to visit when it has reached the limit. Queued links that
    were already reached by following a redirect from another link are not counted as left to visit.

    Args:
        queue (list[str]): the queue of pages to visit
        visited (dict[str, str]): the visited pages

    Returns:
        bool: true there there are pages left to visit
    """
    return any(page_key(link) not in visited for link in queue)


def _looks_like_a_site_wide_block(
    forbidden_count: int, batch_size: int, batch_403_threshold: int, batch_403_ratio: float
) -> bool:
    """Checks whether a batch had so many forbidden (403) pages that the website is blocking the crawler, rather than
    only some of its pages being private (e.g. staff-only links).

    A batch can hold thousands of pages, so a fixed count alone would flag a normal website with many private links.
    The forbidden pages must also be a large share of the batch.

    Args:
        forbidden_count: How many pages in the batch returned 403.
        batch_size: How many pages were in the batch.
        batch_403_threshold: The fewest forbidden pages that can count as a block.
        batch_403_ratio: The smallest share of the batch (0 to 1) that must be forbidden to count as a block.

    Returns:
        True if the batch looks like the website blocking the crawler.
    """
    return forbidden_count >= batch_403_threshold and forbidden_count >= batch_size * batch_403_ratio


async def crawl_site(
    client: httpx2.AsyncClient,
    url: str,
    max_pages: int = 5000,
    max_concurrent: int = 10,
    delay: float = 0,
    batch_403_threshold: int = 20,
    batch_403_ratio: float = 0.5,
) -> set[str]:
    """Asynchronously crawls a website starting from an entry URL up to a maximum page limit.

    Traverses internal links in batched concurrent async requests, tracking visited and queued
    URLs. Monitors batch HTTP responses to detect site-wide blockades or firewall restrictions.

    A website with more pages than the limit is refused rather than partly crawled, as comparing
    a different partial crawl each scan would report pages being added and removed that never were.

    Pages are tracked by their `page_key` (visited maps each page's key to the URL it was visited at),
    so a page linked as both example.com/a and www.example.com/a is only visited once.

    Args:
        client: The HTTP client instance used to execute network requests.
        url: The entry point URL string from which the crawler discovers links.
        max_pages: The most unique internal pages the crawler will visit. Defaults to 5000.
        max_concurrent: The maximum number of concurrent HTTP requests permitted.
            Defaults to 10.
        delay: The time in seconds to pause before fetching individual URLs. Defaults to 0.
        batch_403_threshold: The fewest 403 Forbidden responses in a single batch that can trigger a
            site-wide block exception. Defaults to 20.
        batch_403_ratio: The smallest share of a batch (0 to 1) that must be 403 Forbidden responses to
            trigger a site-wide block exception. Defaults to 0.5.

    Returns:
        A set of normalized internal URL strings visited during the crawl, one for each page.

    Raises:
        TrafficError: If a single batch encounters at least batch_403_threshold 403 Forbidden responses, making
            up at least batch_403_ratio of the batch, indicating firewall blocking or access denial.
        WebsiteUnavailableError: If the home page could not be loaded (e.g. it returned 403, 404 or 500), so no page
            was visited. Otherwise every page would look removed.
        WebsiteTooLargeError: If the limit is reached while there are still pages left to visit.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    url = normalise_url(url)
    visited: dict[str, str] = {}
    queued: set[str] = {page_key(url)}
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

            visited.setdefault(page_key(visited_url), normalise_url(visited_url))
            for link in internal_links:
                link_page: str = page_key(link)
                if link_page not in visited and link_page not in queued:
                    queued.add(link_page)
                    queue.append(link)

        if _looks_like_a_site_wide_block(batch_403_count, len(batch), batch_403_threshold, batch_403_ratio):
            logger.error(
                f"Site-wide block detected: Encountered {batch_403_count} 403 Forbidden responses "
                f"in a single batch of {len(batch)} pages."
            )
            raise TrafficError(url, 403)

    if not visited:
        raise WebsiteUnavailableError(url)
    if _pages_left_to_visit(queue, visited):
        raise WebsiteTooLargeError(url, max_pages)

    logger.info(f"Crawl completed. Exhausted all discoverable links. Total visited: {len(visited)}")
    return set(visited.values())
