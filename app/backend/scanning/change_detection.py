import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import NamedTuple

from httpx2 import AsyncClient, HTTPStatusError, RequestError
from pydantic import HttpUrl

from app.backend.crawler.links import extract_links_from_html, separate_document_links
from app.backend.crawler.page_fetcher import fetch_content_from_url
from app.backend.crawler.site_crawler import crawl_site
from app.backend.diff_checker.content_diff import (
    compare_page_content,
    find_link_difference,
    has_lost_most_content,
    without_ignored_text,
)
from app.backend.diff_checker.models import DiffSettings, PageContent
from app.backend.diff_checker.page_parser import main_content_html, parse_html
from app.core.config import config
from app.core.errors import (
    MostPagesMissingError,
    NotAWebPageError,
    StandInPageError,
    TrafficError,
    WebConnectionError,
)
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.website_models import URL_LIST_ADAPTER, WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES: int = config.web_crawler_default_max_pages
DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures
STAND_IN_FAILURES_BEFORE_ACCEPTING: int = config.critical_page_stand_in_failures_before_accepting
STAND_IN_PAGE_REASON: str = "Most of the page's content is missing, so it may be blocked or under maintenance"

type UnreachablePageError = (
    HTTPStatusError | RequestError | TrafficError | WebConnectionError | StandInPageError | NotAWebPageError
)
UNREACHABLE_PAGE_ERRORS: tuple[type[UnreachablePageError], ...] = (
    HTTPStatusError,
    RequestError,
    TrafficError,
    WebConnectionError,
    StandInPageError,
    NotAWebPageError,  # The page is now a file (e.g. a PDF), so it can no longer be read as a page
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


def _find_url_changes(
    previous_urls: Sequence[HttpUrl], main_content_urls: Sequence[HttpUrl], page_urls: Sequence[HttpUrl]
) -> tuple[list[HttpUrl], list[HttpUrl]]:
    """Finds the links (or documents) added to a page's main content, and those removed from the page altogether.

    A link that is still somewhere on the page (e.g. it moved into the menu) is not reported as removed, it simply
    stops being watched. This also stops the first scan after only the main content was watched reporting every menu
    link saved before then as removed.

    Args:
        previous_urls: The URLs saved by the last scan.
        main_content_urls: The URLs in the page's main content now.
        page_urls: The URLs anywhere on the page now, including its navigation and side panels.

    Returns:
        The added URLs and the removed URLs.
    """
    added_urls, _ = _find_url_difference(previous_urls, main_content_urls)
    _, removed_urls = _find_url_difference(previous_urls, page_urls)
    return added_urls, removed_urls


def _saved_urls_are_out_of_date(previous_urls: Sequence[HttpUrl], current_urls: Sequence[HttpUrl]) -> bool:
    """Checks whether the saved links (or documents) of a page differ from the ones in its main content now.

    Args:
        previous_urls: The URLs saved by the last scan.
        current_urls: The URLs in the page's main content now.

    Returns:
        True if the saved URLs need replacing.
    """
    added_urls, removed_urls = _find_url_difference(previous_urls, current_urls)
    return bool(added_urls or removed_urls)


class PageLinks(NamedTuple):
    """The links found on a page, split into documents and other links.

    Attributes:
        documents: Links to documents, such as PDFs.
        links: Links to everything else.
    """

    documents: list[HttpUrl]
    links: list[HttpUrl]


def _links_on_page(page_url: str, html: str) -> PageLinks:
    """Finds the web links in some of a page's HTML, split into documents and other links.

    Args:
        page_url: The page's URL after any redirects, which relative links are resolved against.
        html: The HTML to look for links in.

    Returns:
        The page's documents and links.
    """
    document_urls, link_urls = separate_document_links(links=extract_links_from_html(url=page_url, html_content=html))
    return PageLinks(URL_LIST_ADAPTER.validate_python(document_urls), URL_LIST_ADAPTER.validate_python(link_urls))


def _parse_without_ignored_text(
    old_html: str, new_html: str, ignore_rules: Sequence[str]
) -> tuple[PageContent, PageContent]:
    """Reads the text of the saved and new versions of a page, leaving out text matching its ignore rules.

    Args:
        old_html: The page as saved by the last scan.
        new_html: The page as it is now.
        ignore_rules: Regular expressions for text that changes on every scan and is not worth reporting.

    Returns:
        The old and new versions of the page's content.
    """
    return (
        without_ignored_text(parse_html(html=old_html), ignore_rules),
        without_ignored_text(parse_html(html=new_html), ignore_rules),
    )


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

    text_body, page_url = await fetch_content_from_url(client, url=str(stored_page.url))
    main_links: PageLinks = await asyncio.to_thread(_links_on_page, page_url, main_content_html(text_body))

    if init or stored_page.text_body is None:
        return CriticalPageUpdate(
            url=stored_page.url, links=main_links.links, documents=main_links.documents, text_body=text_body
        )

    diff_settings: DiffSettings = DiffSettings.from_config()
    ignore_rules: list[str] = stored_page.ignore_rules or []
    old_content, new_content = await asyncio.to_thread(
        _parse_without_ignored_text, stored_page.text_body, text_body, ignore_rules
    )
    if has_lost_most_content(old_content, new_content, diff_settings):
        if not _has_been_a_stand_in_for_too_long(stored_page):
            raise StandInPageError(str(stored_page.url))
        logger.warning(f"{stored_page.url} has lost most of its content for several scans, so it is accepted as real.")

    links_anywhere: PageLinks = await asyncio.to_thread(_links_on_page, page_url, text_body)
    updates = CriticalPageUpdate(url=stored_page.url)

    updates.recent_documents_added, updates.recent_documents_removed = _find_url_changes(
        previous_urls=stored_page.documents or [],
        main_content_urls=main_links.documents,
        page_urls=links_anywhere.documents,
    )
    if _saved_urls_are_out_of_date(stored_page.documents or [], main_links.documents):
        updates.documents = main_links.documents

    updates.recent_links_added, updates.recent_links_removed = _find_url_changes(
        previous_urls=stored_page.links or [],
        main_content_urls=main_links.links,
        page_urls=links_anywhere.links,
    )
    if _saved_urls_are_out_of_date(stored_page.links or [], main_links.links):
        updates.links = main_links.links

    updates.recent_text_added, updates.recent_text_removed, updates.recent_text_changed = await asyncio.to_thread(
        compare_page_content,
        old_content=old_content,
        new_content=new_content,
        settings=diff_settings,
    )

    if updates.recent_text_added or updates.recent_text_removed or updates.recent_text_changed:
        updates.text_body = text_body

    if not updates.has_changes:
        # Links that moved out of the main content are dropped from the saved page without being reported
        return updates if updates.links is not None or updates.documents is not None else None
    recent_changes = updates.model_dump(exclude_unset=True, exclude={"url", "links", "documents", "text_body"})
    logger.info(f"Changes found on {stored_page.url}: {recent_changes}")
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
    elif isinstance(error, NotAWebPageError):
        reason = f"The page is now a file ({error.content_type}), not a web page"
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
    accept_missing_pages: bool = False,
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
        init (bool, optional): Re-save the website's current state as its baseline. Defaults to False.
        accept_missing_pages (bool, optional): Report pages as removed even when most of the website's pages are
            missing, e.g. because they have been missing for several scans in a row. Defaults to False.

    Returns:
        WebsiteUpdate: A schema object containing updates to critical pages and computed
        differences for internal links (added/removed).

    Raises:
        MostPagesMissingError: If the crawl could not find most of the pages found by the last scan, so the website
            is probably partly down. Nothing is returned to be saved, so they are not reported as removed.
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
            batch_403_threshold=config.web_crawler_batch_403_threshold,
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
        missing_count: int = len(updates.recent_removed_internal_links or [])
        if not accept_missing_pages and _has_lost_most_pages(len(stored_internal_links), missing_count):
            raise MostPagesMissingError(str(stored_website.url), missing_count, len(stored_internal_links))

    if _website_has_been_updated(updates):
        return updates
