"""Finds what has changed on a website since its last scan: its critical pages are checked, then the website is
crawled to find the pages added and removed.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence

from httpx2 import AsyncClient
from pydantic import HttpUrl

from app.backend.crawler.site_crawler import crawl_site
from app.backend.scanning.critical_page_checks import find_url_difference, gather_critical_page_updates
from app.core.config import config
from app.core.errors import MostPagesMissingError, TrafficError, WebConnectionError, WebsiteTooLargeError
from app.models.scan_result_models import CriticalPageScanResult, WebsiteScanResult
from app.models.website_models import URL_LIST_ADAPTER, WebsiteRead

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES: int = config.web_crawler_default_max_pages

type CrawlFailure = TrafficError | WebConnectionError | MostPagesMissingError | WebsiteTooLargeError


class CrawlFailedError(Exception):
    """Exception raised when a website's crawl fails (e.g. it was rate limited, could not be reached, most of its
    pages were missing or it had too many pages) after its critical pages were checked.

    It carries what the critical pages' check found, so it can still be saved and reported with the failed scan,
    rather than being thrown away until a scan's crawl succeeds, or the pages being loaded again.

    Attributes:
        error: Why the crawl failed.
        critical_page_updates: What the critical pages' check found, or None if nothing changed.
    """

    def __init__(self, error: CrawlFailure, critical_page_updates: WebsiteScanResult | None) -> None:
        self.error: CrawlFailure = error
        self.critical_page_updates: WebsiteScanResult | None = critical_page_updates
        super().__init__(str(error))


def _has_lost_most_pages(known_count: int, missing_count: int) -> bool:
    """Checks whether a crawl found so few of a website's known pages that the website is probably partly down
    (e.g. a section returning errors), rather than really having removed them.

    A website with only a few pages can lose most of them for real, so it is not checked.

    Args:
        known_count: How many pages the last scan found.
        missing_count: How many of those pages this crawl did not find.

    Returns:
        True if more than the allowed share of the known pages is missing.
    """
    return (
        known_count >= config.web_crawler_min_known_pages_to_check_missing
        and missing_count > known_count * config.web_crawler_max_missing_pages_ratio
    )


def _website_has_been_updated(updates: WebsiteScanResult) -> bool:
    """Checks whether a scan found anything to save: a critical page's result, a first list of pages, or a change.

    Args:
        updates: What the scan found.

    Returns:
        True if there is something to save.
    """
    return bool(updates.critical_page_updates or updates.initial_internal_links is not None or updates.has_changes)


async def get_critical_page_only_updates(
    client: AsyncClient,
    stored_website: WebsiteRead,
    init: bool = False,
) -> WebsiteScanResult | None:
    """Checks a website's critical pages for changes, without crawling the rest of the website.

    Used for inactive websites (e.g. one too large to crawl), whose critical pages are still watched.

    Args:
        client (AsyncClient): The HTTP client used for fetching page content.
        stored_website (WebsiteRead): The current state of the website retrieved from the database.
        init (bool, optional): Re-save each critical page's current state as its baseline. Defaults to False.

    Returns:
        WebsiteScanResult | None: The updates to the website's critical pages, or None if nothing changed.
    """
    critical_page_updates = await gather_critical_page_updates(client, stored_website.critical_pages, init)
    return _critical_page_only_update(stored_website, critical_page_updates)


def _without_failed_checks(
    critical_page_updates: dict[uuid.UUID, CriticalPageScanResult],
) -> dict[uuid.UUID, CriticalPageScanResult]:
    """Leaves out the critical pages that could not be checked, keeping changes, baselines and pages back after failing.

    When a crawl fails because the website rate limited it, could not be reached or was partly down, its critical
    pages failing too is the same problem, which the scan already reports. Counting those failures as well would
    report the pages as unreachable after a couple of such scans.

    Args:
        critical_page_updates: The updates for each critical page that changed, saved a baseline, failed or recovered.

    Returns:
        The updates of the pages that were checked.
    """
    return {page_id: update for page_id, update in critical_page_updates.items() if not update.consecutive_failures}


def _critical_page_only_update(
    stored_website: WebsiteRead, critical_page_updates: dict[uuid.UUID, CriticalPageScanResult]
) -> WebsiteScanResult | None:
    """Wraps what a check of a website's critical pages found as an update of the website.

    Args:
        stored_website: The current state of the website retrieved from the database.
        critical_page_updates: The updates for each critical page that changed, saved a baseline, failed or recovered.

    Returns:
        The update, or None if nothing changed.
    """
    updates = WebsiteScanResult(critical_page_updates=critical_page_updates or None)
    return updates if _website_has_been_updated(updates) else None


async def get_website_updates(
    client: AsyncClient,
    stored_website: WebsiteRead,
    stored_internal_links: Sequence[HttpUrl],
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
    init: bool = False,
    accept_missing_pages: bool = False,
) -> WebsiteScanResult | None:
    """Crawls a website to detect changes in internal links and updates its monitored critical pages.

    Iterates through all associated critical pages to fetch their updates, and crawls the primary
    website URL to map its current internal linking structure. Computes differences in internal
    links compared to the stored state.

    Args:
        client (AsyncClient): The HTTP client used for web crawling and page fetching.
        stored_website (WebsiteRead): The current state of the website retrieved from the database.
        stored_internal_links (Sequence[HttpUrl]): The URLs of the website's internal links saved by its last scan.
        max_pages (int | None): Maximum number of pages to crawl. Overrides the default if provided.
        delay (float | None): Delay between requests. Uses the website's recommended delay if None.
        concurrent (int | None): Maximum concurrent requests. Uses the website's recommended concurrency if None.
        init (bool, optional): Re-save the website's current state as its baseline. Defaults to False.
        accept_missing_pages (bool, optional): Report pages as removed even when most of the website's pages are
            missing, e.g. because they have been missing for several scans in a row. Defaults to False.

    Returns:
        WebsiteScanResult: A schema object containing updates to critical pages and computed
        differences for internal links (added/removed).

    Raises:
        CrawlFailedError: If the crawl was rate limited, could not reach the website, could not find most of the
            pages found by the last scan (so the website is probably partly down), or found the website has more pages
            than the crawler will scan. It carries what the critical pages' check found, so that can still be saved,
            but none of the crawl's pages are reported as removed. Critical pages that could not be checked are left
            out, unless the website was too large, as then the website did answer.
    """
    updates = WebsiteScanResult()
    critical_page_updates: dict[uuid.UUID, CriticalPageScanResult] = await gather_critical_page_updates(
        client=client,
        critical_pages=stored_website.critical_pages,
        init=init,
    )
    updates.critical_page_updates = critical_page_updates or None

    try:
        current_internal_links: list[HttpUrl] = URL_LIST_ADAPTER.validate_python(
            await crawl_site(
                client=client,
                url=str(stored_website.url),
                delay=delay or stored_website.recommended_delay,
                max_concurrent=concurrent or stored_website.recommended_concurrent,
                max_pages=max_pages or DEFAULT_MAX_PAGES,
                batch_403_threshold=config.web_crawler_batch_403_threshold,
                batch_403_ratio=config.web_crawler_batch_403_ratio,
            )
        )
    except WebsiteTooLargeError as error:
        raise CrawlFailedError(error, _critical_page_only_update(stored_website, critical_page_updates)) from error
    except (TrafficError, WebConnectionError) as error:  # Most pages missing is only found once the crawl is done
        checked_pages: dict[uuid.UUID, CriticalPageScanResult] = _without_failed_checks(critical_page_updates)
        raise CrawlFailedError(error, _critical_page_only_update(stored_website, checked_pages)) from error

    if init or not stored_internal_links:
        updates.initial_internal_links = current_internal_links
        logger.info(f"{len(updates.initial_internal_links)=}")
    else:
        # Comparing tens of thousands of pages takes about a second, so it is done in a thread, keeping the dashboard
        # responsive
        updates.recent_added_internal_links, updates.recent_removed_internal_links = await asyncio.to_thread(
            find_url_difference, stored_internal_links, current_internal_links
        )
        # Only the counts are logged, as a website can have thousands of pages
        added_count: int = len(updates.recent_added_internal_links or [])
        missing_count: int = len(updates.recent_removed_internal_links or [])
        logger.info(f"{stored_website.url}: {added_count} pages added and {missing_count} pages removed.")
        if not accept_missing_pages and _has_lost_most_pages(len(stored_internal_links), missing_count):
            raise CrawlFailedError(
                MostPagesMissingError(str(stored_website.url), missing_count, len(stored_internal_links)),
                _critical_page_only_update(stored_website, _without_failed_checks(critical_page_updates)),
            )

    if _website_has_been_updated(updates):
        return updates
