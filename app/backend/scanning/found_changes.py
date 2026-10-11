from collections.abc import Iterator, Sequence

from pydantic import HttpUrl

from app.models.scan_result_models import CriticalPageScanResult, WebsiteScanResult
from app.models.scan_run_models import ChangeCreate, ChangeKind


def _url_changes(
    kind: ChangeKind, urls: Sequence[HttpUrl] | None, page_url: HttpUrl | None = None
) -> Iterator[ChangeCreate]:
    """Makes a change for each link, document or internal page that was added or removed.

    Args:
        kind: Whether they were added or removed, and what they are.
        urls: The URLs that were added or removed, if any.
        page_url: The critical page they were found on, or None for the website's internal pages.

    Yields:
        A change for each URL.
    """
    for url in urls or []:
        yield ChangeCreate(kind=kind, page_url=page_url, url=url)


def _critical_page_changes(page_result: CriticalPageScanResult) -> Iterator[ChangeCreate]:
    """Makes a change for each thing a scan found on one critical page.

    A page that has just failed enough checks in a row is reported once, as unreachable. A page that only saved a
    baseline, or that could be checked again after failing, has nothing to report.

    Args:
        page_result: What the scan found on the page.

    Yields:
        The page's changes: links, then documents, then text added, removed and edited.
    """
    page_url: HttpUrl | None = page_result.url
    if page_result.has_just_reached_failure_limit:
        yield ChangeCreate(
            kind=ChangeKind.PAGE_UNREACHABLE,
            page_url=page_url,
            failure_reason=page_result.last_failure_reason,
            failure_count=page_result.consecutive_failures,
        )
        return

    yield from _url_changes(ChangeKind.LINK_ADDED, page_result.links_added, page_url)
    yield from _url_changes(ChangeKind.LINK_REMOVED, page_result.links_removed, page_url)
    yield from _url_changes(ChangeKind.DOCUMENT_ADDED, page_result.documents_added, page_url)
    yield from _url_changes(ChangeKind.DOCUMENT_REMOVED, page_result.documents_removed, page_url)
    for block in page_result.text_added or []:
        yield ChangeCreate(kind=ChangeKind.TEXT_ADDED, page_url=page_url, new_block=block)
    for block in page_result.text_removed or []:
        yield ChangeCreate(kind=ChangeKind.TEXT_REMOVED, page_url=page_url, old_block=block)
    for edit in page_result.text_changed or []:
        yield ChangeCreate(
            kind=ChangeKind.TEXT_CHANGED,
            page_url=page_url,
            old_block=edit.old_block,
            new_block=edit.new_block,
            similarity=edit.similarity,
        )


def changes_found_by(scan_result: WebsiteScanResult | None) -> list[ChangeCreate]:
    """Lists everything a scan found, to save in the website's history.

    Baselines (e.g. a new website's first crawl) are not changes, so they are left out.

    Args:
        scan_result: What the scan found, or None if nothing changed.

    Returns:
        The website's added and removed internal pages, then the changes on each critical page, in the order the
        pages were checked.
    """
    if scan_result is None:
        return []
    return [
        *_url_changes(ChangeKind.INTERNAL_LINK_ADDED, scan_result.internal_links_added),
        *_url_changes(ChangeKind.INTERNAL_LINK_REMOVED, scan_result.internal_links_removed),
        *(
            change
            for page_result in (scan_result.critical_page_results or {}).values()
            for change in _critical_page_changes(page_result)
        ),
    ]
