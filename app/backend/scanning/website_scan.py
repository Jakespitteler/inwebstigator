import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from httpx2 import AsyncClient
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.backend.scanning.change_detection import (
    CrawlFailedError,
    get_critical_page_only_updates,
    get_website_updates,
)
from app.backend.scanning.found_changes import changes_found_by
from app.backend.scanning.scan_queue import scan_queue
from app.core.config import config
from app.core.errors import (
    MostPagesMissingError,
    TrafficError,
    WebConnectionError,
    WebsiteTooLargeError,
    WebsiteUnavailableError,
)
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.scan_run_models import ChangeCreate, ScanRunCreate, ScanRunRead, ScanStatus
from app.models.website_models import URL_LIST_ADAPTER, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)

MISSING_PAGES_ACCEPTED_AFTER: str = (
    f"If they are still missing after {config.web_crawler_missing_pages_scans_before_accepting} scans in a row, "
    "they will be reported as removed."
)


async def _find_updates(
    client: AsyncClient,
    website: WebsiteRead,
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
    init: bool,
) -> WebsiteUpdate | None:
    """Finds what has changed on a website since its last scan.

    An inactive website only has its critical pages checked, as crawling the rest of the website
    is what it has been switched off from (e.g. because it has too many pages to crawl).

    Args:
        client: The HTTP client for making web requests.
        website: The website to check.
        max_pages: The most pages to crawl, or None for the default.
        delay: Seconds to wait between requests, or None for the website's own.
        concurrent: The most requests at once, or None for the website's own.
        init: Re-save the website's current state as its baseline.

    Returns:
        The updates found, or None if nothing changed.
    """
    if not website.active:
        return await get_critical_page_only_updates(client, website, init)

    with db_context() as session:
        stored_internal_links: list[HttpUrl] = URL_LIST_ADAPTER.validate_python(
            InternalLinkService(session).get_urls_for_website(website.id)
        )
        accept_missing_pages: bool = _has_had_pages_missing_for_too_long(session, website.id)
    return await get_website_updates(
        client, website, stored_internal_links, max_pages, delay, concurrent, init, accept_missing_pages
    )


def _has_had_pages_missing_for_too_long(session: Session, website_id: uuid.UUID) -> bool:
    """Checks whether a website's last few scans all found most of its pages missing.

    A website that is partly down usually comes back by the next scan. Pages that stay missing scan after scan have
    really gone (e.g. the website was restructured), so they are then reported as removed.

    Args:
        session: The open database session.
        website_id: The website to check.

    Returns:
        True if enough scans in a row found most of its pages missing.
    """
    scans_needed: int = config.web_crawler_missing_pages_scans_before_accepting
    latest_scans: list[ScanRunRead] = ScanRunService(session).get_latest_for_website(website_id, limit=scans_needed)
    return len(latest_scans) == scans_needed and all(
        scan_run.status is ScanStatus.PAGES_MISSING for scan_run in latest_scans
    )


def record_scan(
    session: Session,
    website: WebsiteRead,
    status: ScanStatus,
    message: str | None = None,
    changes: Sequence[ChangeCreate] = (),
) -> ScanRunRead:
    """Adds a scan to its website's history, then deletes the website's scans older than the ones kept.

    Args:
        session: The open database session, so the scan is saved together with whatever else the scan saved.
        website: The website that was scanned.
        status: How the scan went.
        message: What the app did about a scan that did not go normally, e.g. putting the website on cooldown.
        changes: What the scan found.

    Returns:
        The saved scan.
    """
    scan_run_service = ScanRunService(session)
    scan_run: ScanRunRead = scan_run_service.create(
        ScanRunCreate(
            website_id=website.id,
            scanned_at=datetime.now(UTC),
            status=status,
            message=message,
            changes=list(changes),
        )
    )
    scan_run_service.delete_older_scans(website.id, keep=config.scans_kept_per_website)
    return scan_run


def _handle_scan_failure(
    website: WebsiteRead,
    status: ScanStatus,
    handle_failure: Callable[[WebsiteService], str],
    found_before_failing: WebsiteUpdate | None = None,
) -> ScanRunRead:
    """Saves how the app responds to a failed scan (e.g. a cooldown), then records the scan with a message saying
    what was done.

    What the scan found before it failed (e.g. changes on the critical pages) is saved and recorded with it, so it is
    reported now rather than only once a scan succeeds.

    Args:
        website: The website whose scan failed.
        status: How the scan failed.
        handle_failure: Makes the response to the failure and describes it, e.g. `WebsiteService.handle_traffic_error`.
        found_before_failing: What the scan found before it failed, or None if nothing changed.

    Returns:
        The recorded scan.
    """
    with db_context() as session:
        website_service = WebsiteService(session)
        if found_before_failing:
            website_service.update(id=website.id, model_update=found_before_failing)
        action_message: str = handle_failure(website_service)
        scan_run: ScanRunRead = record_scan(
            session, website, status, action_message, changes_found_by(found_before_failing)
        )
    return scan_run


async def _record_failed_crawl(
    website: WebsiteRead, failure: CrawlFailedError, delay: float | None, concurrent: int | None
) -> ScanRunRead:
    """Responds to a crawl that failed, saving what the critical pages' check found and recording the scan.

    A website that rate limits the crawler is throttled and put on cooldown, an unreachable or partly down website
    is put on cooldown, and a website with more pages than the crawler will scan is deactivated.

    Args:
        website: The website whose crawl failed.
        failure: Why the crawl failed, and what the critical pages' check found before it did.
        delay: Seconds to wait between requests that the scan was given, or None for the website's own.
        concurrent: The most requests at once that the scan was given, or None for the website's own.

    Returns:
        The recorded scan.

    Raises:
        TrafficError: If the website rate limited a scan that was given its own delay or concurrency.
    """
    found_before_failing: WebsiteUpdate | None = failure.critical_page_updates
    match failure.error:
        case TrafficError() as error:
            logger.error("Temporary ban or severe rate limit detected for %s: %s", website.url, error)
            if delay or concurrent:
                raise TrafficError(
                    url=str(website.url),
                    status_code=error.status_code,
                    message="Scan aborted, try increasing delay or reducing concurrent (may be banned)",
                ) from error
            return await asyncio.to_thread(
                _handle_scan_failure,
                website,
                ScanStatus.TRAFFIC_ERROR,
                lambda websites: websites.handle_traffic_error(website),
                found_before_failing,
            )
        case WebsiteUnavailableError() as error:
            logger.error("Home page could not be loaded: %s", error)
            unavailable_message: str = str(error)
            return await asyncio.to_thread(
                _handle_scan_failure,
                website,
                ScanStatus.CONNECTION_ERROR,
                lambda websites: f"{unavailable_message} {websites.handle_connection_error(website.id)}",
                found_before_failing,
            )
        case WebConnectionError() as error:
            logger.error("Site unreachable: %s", error)
            return await asyncio.to_thread(
                _handle_scan_failure,
                website,
                ScanStatus.CONNECTION_ERROR,
                lambda websites: websites.handle_connection_error(website.id),
                found_before_failing,
            )
        case MostPagesMissingError() as error:
            logger.warning("Most pages missing: %s", error)
            missing_message: str = f"{error} {MISSING_PAGES_ACCEPTED_AFTER}"
            return await asyncio.to_thread(
                _handle_scan_failure,
                website,
                ScanStatus.PAGES_MISSING,
                lambda websites: f"{missing_message} {websites.handle_connection_error(website.id)}",
                found_before_failing,
            )
        case WebsiteTooLargeError() as error:
            logger.warning("Website too large to scan: %s", error)
            return await asyncio.to_thread(
                _deactivate_too_large_website, website, error.max_pages, found_before_failing
            )


def _deactivate_too_large_website(
    website: WebsiteRead, max_pages: int, critical_page_updates: WebsiteUpdate | None
) -> ScanRunRead:
    """Deactivates a website with more pages than the crawler will scan, then saves what its critical pages' check
    found, as they are still watched while it is inactive.

    The critical pages were already checked before the crawl, so they are not loaded again. Both are recorded as one
    scan, whose report says the website was deactivated and lists any changes found on its critical pages.

    Args:
        website: The website found to be too large.
        max_pages: The most pages the crawler would scan.
        critical_page_updates: What the critical pages' check found, or None if nothing changed.

    Returns:
        The recorded scan.
    """
    with db_context() as session:
        too_large_message: str = WebsiteService(session).handle_too_large(website.id, max_pages)
    return _save_updates(website, critical_page_updates, ScanStatus.TOO_LARGE, too_large_message)


def _save_updates(
    website: WebsiteRead,
    website_updates: WebsiteUpdate | None,
    status: ScanStatus = ScanStatus.SUCCESS,
    message: str | None = None,
) -> ScanRunRead:
    """Saves what a scan found, including any new baselines, clears the website's failed attempts and records the
    scan in the website's history.

    It is all saved together, so a change never becomes the new baseline without also being recorded.

    Args:
        website: The website that was scanned.
        website_updates: What the scan found, or None if nothing changed.
        status: How the scan went. Defaults to a normal scan.
        message: What the app did about a scan that did not go normally.

    Returns:
        The recorded scan.
    """
    with db_context() as session:
        website_service = WebsiteService(session)
        if website_updates:
            website_service.update(id=website.id, model_update=website_updates)
        website_service.reset_failed_attempts(website.id)
        scan_run: ScanRunRead = record_scan(session, website, status, message, changes_found_by(website_updates))
    return scan_run


async def scan_website(
    client: AsyncClient,
    website: WebsiteRead,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
    init: bool = False,
) -> ScanRunRead:
    """Scans a website for changes once any scans requested before it have finished, saves what it found, and
    records the scan in the website's history.

    A website that rate limits the crawler is throttled and put on cooldown, an unreachable website is put on
    cooldown, and a website with more pages than the crawler will scan is deactivated. What its critical pages' check
    found is still saved when the crawl fails. An inactive website only has its critical pages checked.

    Args:
        client: The HTTP client for making web requests.
        website: The website to scan.
        max_pages: The most pages to crawl, or None for the default.
        delay: Seconds to wait between requests, or None for the website's own.
        concurrent: The most requests at once, or None for the website's own.
        init: Re-save the website's current state as its baseline. Not needed for new websites, which are
            baselined automatically.

    Returns:
        The recorded scan, with what it found. Its report has not been emailed yet.

    Raises:
        TrafficError: If the website rate limited a scan that was given its own delay or concurrency.
        ScanAlreadyQueuedError: If the website is already queued or being scanned.
        ScanCancelledError: If the scan was cancelled before it finished.
    """
    try:
        website_updates: WebsiteUpdate | None = await scan_queue.run(
            str(website.url), lambda: _find_updates(client, website, max_pages, delay, concurrent, init)
        )
    except CrawlFailedError as failure:
        return await _record_failed_crawl(website, failure, delay, concurrent)

    # Saving a large website's links can take a few seconds, so it is done in a thread, keeping the dashboard responsive
    scan_run: ScanRunRead = await asyncio.to_thread(_save_updates, website, website_updates)
    if not scan_run.has_report:
        logger.info("No changes found for %s", website.url)
    return scan_run
