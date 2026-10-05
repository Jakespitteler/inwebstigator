import asyncio
import logging
import uuid
from collections.abc import Sequence

from httpx2 import AsyncClient, HTTPStatusError, RequestError

from app.backend.diff_checker import compare_page_content, find_link_difference
from app.backend.site_crawler import crawl_site
from app.backend.utils.html_parser import parse_html
from app.backend.utils.http_client import fetch_content_from_url
from app.backend.utils.links import extract_links_from_html, separate_document_links
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES: int = config.web_crawler_default_max_pages
DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
BATCH_402_THRESHOLD_SECONDS: int = config.web_crawler_batch_402_threshold_seconds
ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures

# The ways fetching a critical page can fail. Anything else is a bug, so is not counted as the page being unreachable.
FETCH_ERRORS = (HTTPStatusError, RequestError, TrafficError, WebConnectionError)


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
    It also parses the textual content of the page to detect structural or text changes.

    Args:
        client (AsyncClient): The HTTP client used for fetching page content.
        stored_page (CriticalPageRead): The current state of the critical page retrieved from the database.

    Returns:
        CriticalPageUpdate: A schema object containing the updated fields (text_body, links, documents)
        and the computed recent differences (added/removed links, text changes).
    """

    text_body, _ = await fetch_content_from_url(client, url=stored_page.url)
    documents, links = separate_document_links(
        links=extract_links_from_html(url=stored_page.url, html_content=text_body)
    )

    if init or stored_page.text_body is None:
        return CriticalPageUpdate(url=stored_page.url, links=links, documents=documents, text_body=text_body)

    updates = CriticalPageUpdate(url=stored_page.url)

    updates.recent_documents_added, updates.recent_documents_removed = find_link_difference(
        previous_state=stored_page.documents or [],
        current_state=documents,
    )
    if updates.recent_documents_added or updates.recent_documents_removed:
        updates.documents = documents

    updates.recent_links_added, updates.recent_links_removed = find_link_difference(
        previous_state=stored_page.links or [],
        current_state=links,
    )
    if updates.recent_links_added or updates.recent_links_removed:
        updates.links = links

    updates.recent_text_added, updates.recent_text_removed, updates.recent_text_changed = compare_page_content(
        old_content=parse_html(html=stored_page.text_body or ""),
        new_content=parse_html(html=text_body),
    )

    if updates.recent_text_added or updates.recent_text_removed or updates.recent_text_changed:
        updates.text_body = text_body

    if not updates.has_changes:
        return None
    recent_changes = updates.model_dump(exclude_unset=True, exclude={"url", "links", "documents", "text_body"})
    logger.info(f"Changes found on {stored_page.url}: {recent_changes}")
    return updates


def _failed_check_update(
    stored_page: CriticalPageRead, error: HTTPStatusError | RequestError | TrafficError | WebConnectionError
) -> CriticalPageUpdate:
    """Records a failed check of a critical page. Its saved content is kept to compare with once it is back.

    A missing (404) or gone (410) page counts as having failed enough checks to be reported straight away.

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
    elif isinstance(error, WebConnectionError):
        reason = "Connection failed or timed out"
    else:
        reason = "Request failed"

    failures: int = stored_page.consecutive_failures + 1
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
    check instead of stopping the rest of the pages being checked. A page that can be checked again has
    its failure count reset.

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
        if isinstance(result, FETCH_ERRORS):
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
        max_pages (int | None): Maximum number of pages to crawl. Overrides the default if provided.
        delay (float | None): Delay between requests. Uses the website's recommended delay if None.
        concurrent (int | None): Maximum concurrent requests. Uses the website's recommended concurrency if None.

    Returns:
        WebsiteUpdate: A schema object containing updates to critical pages and computed
        differences for internal links (added/removed).
    """
    updates = WebsiteUpdate(url=stored_website.url)
    critical_page_updates = await _gather_critical_page_updates(client, stored_website.critical_pages, init)
    updates.critical_page_updates = critical_page_updates or None

    current_internal_links: list[str] = list(
        await crawl_site(
            client,
            url=stored_website.url,
            delay=delay or stored_website.recommended_delay,
            max_concurrent=concurrent or stored_website.recommended_concurrent,
            max_pages=max_pages or DEFAULT_MAX_PAGES,
            batch_403_threshold=BATCH_402_THRESHOLD_SECONDS,
        )
    )
    if init or not stored_website.internal_links:
        updates.initial_internal_links = current_internal_links
        logger.info(f"{len(updates.initial_internal_links)=}")
    else:
        updates.recent_added_internal_links, updates.recent_removed_internal_links = find_link_difference(
            previous_state=[link.url for link in stored_website.internal_links],
            current_state=current_internal_links,
        )
        logger.info(f"{updates.recent_added_internal_links=}")
        logger.info(f"{updates.recent_removed_internal_links=}")

    if _website_has_been_updated(updates):
        return updates
