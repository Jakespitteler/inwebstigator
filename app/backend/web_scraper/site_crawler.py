import asyncio
import logging
from collections.abc import Awaitable, Iterator

import httpx2

from app.backend.web_scraper.errors import TrafficError, WebConnectionError
from app.backend.web_scraper.utils import (
    extract_links,
    fetch_content_from_url,
    is_internal_web_page,
    normalise_url,
)

logger = logging.getLogger(__name__)


async def fetch_and_extract(
    client: httpx2.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    delay: float | None = None,
    base_url: str | None = None,
) -> tuple[str, list[str], int | None]:
    """Safely fetches HTML and extracts internal links under a concurrency limit.

    Args:
        client (httpx2.AsyncClient): the web client.
        url (str): the url to fetch and extract internal links from.
        semaphore (asyncio.Semaphore): The concurrency limiter.
        delay (float): the time to wait in between before fetching content.

    Returns:
        tuple[str, list[str], int | None]: (the url, all of the internal links on that page, the status
            code from the request)

    Raises:
        TrafficError: If the server returns a rate-limiting or throttling status code (429, 502, 503, 504).
        WebConnectionError: If a timeout or connection failure occurs.
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

            links: list[str] = extract_links(
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
    """Crawls a website asynchronously starting from url up to max_pages.

    Args:
        client (httpx2.httpx2.AsyncClient): The web client
        url (str): The website for the crawler to extract links from.
        max_pages (int, optional): The limit on pages able to be visited before the crawler stops. Defaults to 5000.
        max_concurrent (int, optional): The maximum amount of URLs to visit concurrently. Defaults to 10.
        delay (float): The time to wait in between scraping URLs.

    Returns:
        set[str]: All internal links in the website

    Raises:
        TrafficError: If a batch encounters a volume of 403 Forbidden responses exceeding the block_threshold,
            indicating a firewall or site-wide ban.
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
            fetch_and_extract(client=client, base_url=url, url=current_url, semaphore=semaphore, delay=delay)
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
