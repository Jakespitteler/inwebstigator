import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime

from httpx2 import AsyncClient, HTTPStatusError, RequestError
from pydantic import HttpUrl

from app.backend.diff_checker.content_diff import (
    compare_page_content,
    find_link_difference,
    has_lost_most_content,
    without_ignored_text,
)
from app.backend.diff_checker.models import DiffSettings, PageContent
from app.backend.diff_checker.page_parser import parse_html
from app.backend.links import extract_links_from_html, separate_document_links
from app.backend.page_fetcher import fetch_content_from_url
from app.backend.site_crawler import crawl_site
from app.core.config import config
from app.core.errors import StandInPageError, TrafficError, WebConnectionError
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.website_models import URL_LIST_ADAPTER, WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES: int = config.web_crawler_default_max_pages
DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
BATCH_402_THRESHOLD_SECONDS: int = config.web_crawler_batch_402_threshold_seconds
ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures
STAND_IN_FAILURES_BEFORE_ACCEPTING: int = config.critical_page_stand_in_failures_before_accepting
STAND_IN_PAGE_REASON: str = "Most of the page's content is missing, so it may be blocked or under maintenance"

type UnreachablePageError = HTTPStatusError | RequestError | TrafficError | WebConnectionError | StandInPageError
UNREACHABLE_PAGE_ERRORS: tuple[type[UnreachablePageError], ...] = (
    HTTPStatusError,
    RequestError,
    TrafficError,
    WebConnectionError,
    StandInPageError,
)


def _find_url_difference(
    previous_urls: Sequence[HttpUrl], current_urls: Sequence[HttpUrl]
) -> tuple[list[HttpUrl], list[HttpUrl]]:
    """Finds the URLs added and removed since the last scan.

    Both sides are validated URLs, so a URL is not reported as changed just because it was written differently
    (e.g. with a space instead of "%20").

    Args:
        previous_urls: The URLs saved by the last scan.
        current_urls: The URLs found now.

    Returns:
        The added URLs and the removed URLs.
    """
    added_urls, removed_urls = find_link_difference(
        previous_state=[str(url) for url in previous_urls],
        current_state=[str(url) for url in current_urls],
    )
    return URL_LIST_ADAPTER.validate_python(added_urls), URL_LIST_ADAPTER.validate_python(removed_urls)


def _has_been_a_stand_in_for_too_long(stored_page: CriticalPageRead) -> bool:
    """Checks whether a page has looked like a stand-in for so many scans in a row that it is really a redesign.

    A stand-in page (e.g. a browser check) usually goes away by the next scan. A redesign that cut most of the
    page's text looks the same, but never goes away, so after enough scans in a row it is accepted as the real page.

    Args:
        stored_page: The current state of the critical page retrieved from the database.

    Returns:
        True if the page's latest failed checks were all stand-ins and there have been enough of them in a row.
    """
    return (
        stored_page.last_failure_reason == STAND_IN_PAGE_REASON
        and stored_page.consecutive_failures >= STAND_IN_FAILURES_BEFORE_ACCEPTING
    )


def _website_has_been_updated(updates: WebsiteUpdate) -> bool:
    return bool(updates.critical_page_updates or updates.initial_internal_links is not None or updates.has_changes)


async def get_critical_page_updates(
    client: AsyncClient,
    stored_page: CriticalPageRead,
    init: bool = False,
) -> CriticalPageUpdate | None:
    """Fetches the latest content for a critical page and computes the differences from its stored state.

    Analyses the fetched HTML to extract and categorise links (documents vs. regular links),
    then compares them against the previously stored state to identify additions and removals.
    It also parses the textual content of the page to detect text that was added, removed, edited or moved.
    Text matching the page's ignore rules is left out of the comparison.

    Args:
        client (AsyncClient): The HTTP client used for fetching page content.
        stored_page (CriticalPageRead): The current state of the critical page retrieved from the database.
        init (bool, optional): Re-save the page's current state as its baseline. Defaults to False.

    Returns:
        CriticalPageUpdate | None: A schema object containing the updated fields (text_body, links, documents)
        and the computed recent differences (added/removed links, text changes), or None if nothing changed.

    Raises:
        StandInPageError: If the page loaded but has lost most of its text, so it is almost certainly a stand-in
            (e.g. a browser check or maintenance page). The saved page is kept to compare with once it is back,
            unless it has been a stand-in for several scans in a row, when the new content is accepted as real.
    """

    text_body, _ = await fetch_content_from_url(client, url=str(stored_page.url))
    document_urls, link_urls = separate_document_links(
        links=extract_links_from_html(url=str(stored_page.url), html_content=text_body)
    )
    documents: list[HttpUrl] = URL_LIST_ADAPTER.validate_python(document_urls)
    links: list[HttpUrl] = URL_LIST_ADAPTER.validate_python(link_urls)

    if init or stored_page.text_body is None:
        return CriticalPageUpdate(url=stored_page.url, links=links, documents=documents, text_body=text_body)

    diff_settings: DiffSettings = DiffSettings.from_config()
    ignore_rules: list[str] = stored_page.ignore_rules or []
    old_content: PageContent = without_ignored_text(parse_html(html=stored_page.text_body), ignore_rules)
    new_content: PageContent = without_ignored_text(parse_html(html=text_body), ignore_rules)
    if has_lost_most_content(old_content, new_content, diff_settings):
        if not _has_been_a_stand_in_for_too_long(stored_page):
            raise StandInPageError(str(stored_page.url))
        logger.warning(f"{stored_page.url} has lost most of its content for several scans, so it is accepted as real.")

    updates = CriticalPageUpdate(url=stored_page.url)

    updates.recent_documents_added, updates.recent_documents_removed = _find_url_difference(
        previous_urls=stored_page.documents or [],
        current_urls=documents,
    )
    if updates.recent_documents_added or updates.recent_documents_removed:
        updates.documents = documents

    updates.recent_links_added, updates.recent_links_removed = _find_url_difference(
        previous_urls=stored_page.links or [],
        current_urls=links,
    )
    if updates.recent_links_added or updates.recent_links_removed:
        updates.links = links

    updates.recent_text_added, updates.recent_text_removed, updates.recent_text_changed = compare_page_content(
        old_content=old_content,
        new_content=new_content,
        settings=diff_settings,
    )

    if updates.recent_text_added or updates.recent_text_removed or updates.recent_text_changed:
        updates.text_body = text_body

    if not updates.has_changes:
        return None
    recent_changes = updates.model_dump(exclude_unset=True, exclude={"url", "links", "documents", "text_body"})
    logger.info(f"Changes found on {stored_page.url}: {recent_changes}")
    updates.last_changed_at = datetime.now()
    return updates


def _failed_check_update(stored_page: CriticalPageRead, error: UnreachablePageError) -> CriticalPageUpdate:
    """Records a failed check of a critical page. Its saved content is kept to compare with once it is back.

    A missing (404) or gone (410) page counts as having failed enough checks to be reported straight away.
    A stand-in page after a different failure (e.g. the page could not be reached) starts the count again, so only
    stand-ins in a row count towards accepting the page's new content (see `_has_been_a_stand_in_for_too_long`).

    Args:
        stored_page (CriticalPageRead): The current state of the critical page retrieved from the database.
        error: Why the page could not be fetched.

    Returns:
        CriticalPageUpdate: The page's new failure count and the reason it failed.
    """
    status_code: int | None = None
    if isinstance(error, HTTPStatusError):
        status_code = error.response.status_code
    elif isinstance(error, TrafficError):
        status_code = error.status_code

    if status_code:
        reason = f"HTTP {status_code}"
    elif isinstance(error, StandInPageError):
        reason = STAND_IN_PAGE_REASON
    elif isinstance(error, WebConnectionError):
        reason = "Connection failed or timed out"
    else:
        reason = "Request failed"

    failures: int = stored_page.consecutive_failures + 1
    if isinstance(error, StandInPageError) and stored_page.last_failure_reason != STAND_IN_PAGE_REASON:
        failures = 1
    if status_code in {404, 410}:
        failures = max(failures, ALERT_AFTER_FAILURES)
    return CriticalPageUpdate(url=stored_page.url, consecutive_failures=failures, last_failure_reason=reason)


async def _gather_critical_page_updates(
    client: AsyncClient,
    critical_pages: Sequence[CriticalPageRead],
    init: bool,
) -> dict[uuid.UUID, CriticalPageUpdate]:
    """Checks each critical page for changes at the same time.

    Failures are gathered per page, so one broken page (e.g. deleted, now a 404) is recorded as a failed
    check instead of stopping the rest of the pages being checked. Only the errors that mean the page could
    not be reached count as failed checks; anything else is a bug, so the page is skipped and logged instead.
    A page that can be checked again has its failure count reset.

    Args:
        client (AsyncClient): The HTTP client used for fetching page content.
        critical_pages (Sequence[CriticalPageRead]): The stored state of each critical page.
        init (bool): Re-save each page's current state as its baseline.

    Returns:
        dict[uuid.UUID, CriticalPageUpdate]: The updates for each page that changed, saved a baseline,
        failed or recovered, by page ID.
    """
    results = await asyncio.gather(
        *(get_critical_page_updates(client, stored_page, init) for stored_page in critical_pages),
        return_exceptions=True,
    )
    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] = {}
    for critical_page, result in zip(critical_pages, results, strict=True):
        if isinstance(result, UNREACHABLE_PAGE_ERRORS):
            logger.warning(f"Critical page {critical_page.url} could not be fetched this scan: {result!r}")
            critical_page_updates[critical_page.id] = _failed_check_update(critical_page, result)
            continue
        if isinstance(result, BaseException):
            # Anything that is not an Exception (e.g. cancellation on shutdown) must not be swallowed.
            if not isinstance(result, Exception):
                raise result  # e.g. cancellation when the app is shutting down
            logger.warning(
                f"Skipping critical page {critical_page.url} this scan as it could not be checked: {result!r}"
            )
            continue
        if critical_page.consecutive_failures:  # It can be checked again, so reset its failure count
            result = result or CriticalPageUpdate(url=critical_page.url)
            result.consecutive_failures = 0
            result.last_failure_reason = None
        if result is not None:
            critical_page_updates[critical_page.id] = result
    return critical_page_updates


async def get_critical_page_only_updates(
    client: AsyncClient,
    stored_website: WebsiteRead,
    init: bool = False,
) -> WebsiteUpdate | None:
    """Checks a website's critical pages for changes, without crawling the rest of the website.

    Used for inactive websites (e.g. one too large to crawl), whose critical pages are still watched.

    Args:
        client (AsyncClient): The HTTP client used for fetching page content.
        stored_website (WebsiteRead): The current state of the website retrieved from the database.
        init (bool, optional): Re-save each critical page's current state as its baseline. Defaults to False.

    Returns:
        WebsiteUpdate | None: The updates to the website's critical pages, or None if nothing changed.
    """
    critical_page_updates = await _gather_critical_page_updates(client, stored_website.critical_pages, init)
    updates = WebsiteUpdate(url=stored_website.url, critical_page_updates=critical_page_updates or None)
    return updates if _website_has_been_updated(updates) else None


async def get_website_updates(
    client: AsyncClient,
    stored_website: WebsiteRead,
    stored_internal_links: Sequence[HttpUrl],
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
    init: bool = False,
) -> WebsiteUpdate | None:
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

    Returns:
        WebsiteUpdate: A schema object containing updates to critical pages and computed
        differences for internal links (added/removed).
    """
    updates = WebsiteUpdate(url=stored_website.url)
    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] = await _gather_critical_page_updates(
        client=client,
        critical_pages=stored_website.critical_pages,
        init=init,
    )
    updates.critical_page_updates = critical_page_updates or None

    current_internal_links: list[HttpUrl] = URL_LIST_ADAPTER.validate_python(
        await crawl_site(
            client=client,
            url=str(stored_website.url),
            delay=delay or stored_website.recommended_delay,
            max_concurrent=concurrent or stored_website.recommended_concurrent,
            max_pages=max_pages or DEFAULT_MAX_PAGES,
            batch_403_threshold=BATCH_402_THRESHOLD_SECONDS,
        )
    )
    if init or not stored_internal_links:
        updates.initial_internal_links = current_internal_links
        logger.info(f"{len(updates.initial_internal_links)=}")
    else:
        updates.recent_added_internal_links, updates.recent_removed_internal_links = _find_url_difference(
            previous_urls=stored_internal_links,
            current_urls=current_internal_links,
        )
        logger.info(f"{updates.recent_added_internal_links=}")
        logger.info(f"{updates.recent_removed_internal_links=}")
        if updates.recent_added_internal_links or updates.recent_removed_internal_links:
            updates.internal_links_last_changed_at = datetime.now()

    if _website_has_been_updated(updates):
        return updates
