"""Checks a website's critical pages for changes: the links, documents and text added, removed or edited on each page
since the last scan, and which pages could not be checked.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import NamedTuple

from httpx2 import AsyncClient, HTTPStatusError, RequestError
from pydantic import HttpUrl

from app.backend.crawler.links import extract_links_from_html, page_link_base, separate_document_links
from app.backend.crawler.page_fetcher import fetch_content_from_url
from app.backend.diff_checker.content_diff import (
    compare_page_content,
    find_link_difference,
    has_lost_most_content,
    without_ignored_text,
)
from app.backend.diff_checker.models import DiffSettings, PageContent
from app.backend.diff_checker.page_parser import main_content_html, parse_html
from app.core.config import config
from app.core.errors import NotAWebPageError, StandInPageError, TrafficError, WebConnectionError
from app.models.critical_page_models import CriticalPageRead
from app.models.scan_result_models import CriticalPageScanResult
from app.models.website_models import URL_LIST_ADAPTER

logger = logging.getLogger(__name__)

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


def find_url_difference(
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
    added_urls, _ = find_url_difference(previous_urls, main_content_urls)
    _, removed_urls = find_url_difference(previous_urls, page_urls)
    return added_urls, removed_urls


def _saved_urls_are_out_of_date(previous_urls: Sequence[HttpUrl], current_urls: Sequence[HttpUrl]) -> bool:
    """Checks whether the saved links (or documents) of a page differ from the ones in its main content now.

    Args:
        previous_urls: The URLs saved by the last scan.
        current_urls: The URLs in the page's main content now.

    Returns:
        True if the saved URLs need replacing.
    """
    added_urls, removed_urls = find_url_difference(previous_urls, current_urls)
    return bool(added_urls or removed_urls)


class PageLinks(NamedTuple):
    """The links found on a page, split into documents and other links.

    Attributes:
        documents: Links to documents, such as PDFs.
        links: Links to everything else.
    """

    documents: list[HttpUrl]
    links: list[HttpUrl]


def _links_on_page(page_url: str, html: str, main_content_only: bool = False) -> PageLinks:
    """Finds the web links on a page, split into documents and other links.

    Relative links are resolved against the page's `<base href>` if it has one, which is in its `<head>`, so the
    whole page is always given.

    Args:
        page_url: The page's URL after any redirects.
        html: The page's whole HTML.
        main_content_only: Only look in the page's main content (see `main_content_html`), leaving out its
            navigation and side panels. Defaults to the whole page.

    Returns:
        The page's documents and links.
    """
    link_base: str = page_link_base(page_url, html)
    searched_html: str = main_content_html(html) if main_content_only else html
    document_urls, link_urls = separate_document_links(
        links=extract_links_from_html(url=link_base, html_content=searched_html)
    )
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


async def get_critical_page_updates(
    client: AsyncClient,
    stored_page: CriticalPageRead,
    init: bool = False,
) -> CriticalPageScanResult | None:
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
        CriticalPageScanResult | None: A schema object containing the updated fields (text_body, links, documents)
        and the computed recent differences (added/removed links, text changes), or None if nothing changed.

    Raises:
        StandInPageError: If the page loaded but has lost most of its text, so it is almost certainly a stand-in
            (e.g. a browser check or maintenance page). The saved page is kept to compare with once it is back,
            unless it has been a stand-in for several scans in a row, when the new content is accepted as real.
    """

    text_body, page_url = await fetch_content_from_url(client, url=str(stored_page.url))
    main_links: PageLinks = await asyncio.to_thread(_links_on_page, page_url, text_body, True)

    if init or stored_page.text_body is None:
        return CriticalPageScanResult(
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
    updates = CriticalPageScanResult(url=stored_page.url)

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
    # Only how many of each change are logged, as a redesigned page can have hundreds of blocks of text
    change_counts: dict[str, int] = {
        kind: len(changes)
        for kind in CriticalPageScanResult.model_fields
        if kind.startswith("recent_") and (changes := getattr(updates, kind))
    }
    logger.info(f"Changes found on {stored_page.url}: {change_counts}")
    return updates


def _failed_check_update(stored_page: CriticalPageRead, error: UnreachablePageError) -> CriticalPageScanResult:
    """Records a failed check of a critical page. Its saved content is kept to compare with once it is back.

    A missing (404) or gone (410) page counts as having failed enough checks to be reported straight away.
    A stand-in page after a different failure (e.g. the page could not be reached) starts the count again, so only
    stand-ins in a row count towards accepting the page's new content (see `_has_been_a_stand_in_for_too_long`).

    Args:
        stored_page (CriticalPageRead): The current state of the critical page retrieved from the database.
        error: Why the page could not be fetched.

    Returns:
        CriticalPageScanResult: The page's new failure count and the reason it failed.
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
    return CriticalPageScanResult(url=stored_page.url, consecutive_failures=failures, last_failure_reason=reason)


async def gather_critical_page_updates(
    client: AsyncClient,
    critical_pages: Sequence[CriticalPageRead],
    init: bool,
) -> dict[uuid.UUID, CriticalPageScanResult]:
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
        dict[uuid.UUID, CriticalPageScanResult]: The updates for each page that changed, saved a baseline,
        failed or recovered, by page ID.
    """
    results = await asyncio.gather(
        *(get_critical_page_updates(client, stored_page, init) for stored_page in critical_pages),
        return_exceptions=True,
    )
    critical_page_updates: dict[uuid.UUID, CriticalPageScanResult] = {}
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
            result = result or CriticalPageScanResult(url=critical_page.url)
            result.consecutive_failures = 0
            result.last_failure_reason = None
        if result is not None:
            critical_page_updates[critical_page.id] = result
    return critical_page_updates
