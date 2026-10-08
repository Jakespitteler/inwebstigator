import logging
from collections.abc import Callable

from httpx2 import AsyncClient
from pydantic import HttpUrl

from app.backend.change_detection import get_critical_page_only_updates, get_website_updates
from app.backend.email_service.html_bodies import ScanStatus, generate_scan_report_html, join_scan_reports
from app.core.errors import TrafficError, WebConnectionError, WebsiteTooLargeError
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.website_models import URL_LIST_ADAPTER, WebsiteRead, WebsiteUpdate
from app.scanning.scan_queue import scan_queue

logger: logging.Logger = logging.getLogger(__name__)


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
    return await get_website_updates(client, website, stored_internal_links, max_pages, delay, concurrent, init)


def _handle_scan_failure(
    website: WebsiteRead, status: ScanStatus, handle_failure: Callable[[WebsiteService], str]
) -> str:
    """Saves how the app responds to a failed scan (e.g. a cooldown), then writes a report saying what was done.

    Args:
        website: The website whose scan failed.
        status: How the scan failed.
        handle_failure: Makes the response to the failure and describes it, e.g. `WebsiteService.handle_traffic_error`.

    Returns:
        The HTML report.
    """
    with db_context() as session:
        action_message: str = handle_failure(WebsiteService(session))
    return generate_scan_report_html(website, status=status, message=action_message)


async def _deactivate_then_check_critical_pages(
    client: AsyncClient, website: WebsiteRead, max_pages: int, init: bool
) -> str:
    """Deactivates a website with more pages than the crawler will scan, then checks its critical pages,
    which are still watched while it is inactive, rather than leaving them until its next scan.

    The website is inactive by the time its critical pages are checked, so that check cannot find it too large again.

    Args:
        client: The HTTP client for making web requests.
        website: The website found to be too large.
        max_pages: The most pages the crawler would scan.
        init: Re-save the critical pages' current state as their baseline.

    Returns:
        The HTML report saying the website was deactivated, followed by any changes found on its critical pages.

    Raises:
        ScanCancelledError: If the check of the critical pages was cancelled before it finished.
    """
    too_large_report: str = _handle_scan_failure(
        website, ScanStatus.TOO_LARGE, lambda websites: websites.handle_too_large(website.id, max_pages)
    )
    with db_context() as session:
        inactive_website: WebsiteRead = WebsiteService(session).get(website.id)

    critical_page_report: str | None = await scan_website(client, inactive_website, init=init)
    return join_scan_reports(report for report in (too_large_report, critical_page_report) if report)


def _save_updates(website: WebsiteRead, website_updates: WebsiteUpdate | None) -> WebsiteRead:
    """Saves what a scan found, including any new baselines, and clears the website's failed attempts.

    Args:
        website: The website that was scanned.
        website_updates: What the scan found, or None if nothing changed.

    Returns:
        The website as it is now saved.
    """
    with db_context() as session:
        website_service = WebsiteService(session)
        saved_website: WebsiteRead = (
            website_service.update(id=website.id, model_update=website_updates) if website_updates else website
        )
        website_service.reset_failed_attempts(website.id)
    return saved_website


def _only_pages_changed_by(website_updates: WebsiteUpdate, website: WebsiteRead) -> WebsiteRead:
    """Leaves out the critical pages a scan did not change, which still hold the changes an earlier scan reported.

    Args:
        website_updates: What the scan found.
        website: The website as saved after the scan.

    Returns:
        A copy of the website with only the critical pages the scan changed.
    """
    changed_pages = [page for page in website.critical_pages if page.id in website_updates.changed_page_ids]
    return website.model_copy(update={"critical_pages": changed_pages})


async def scan_website(
    client: AsyncClient,
    website: WebsiteRead,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
    init: bool = False,
) -> str | None:
    """Scans a website for changes once any scans requested before it have finished, saves what it found, and
    writes a report.

    A website that rate limits the crawler is throttled and put on cooldown, an unreachable website is put on
    cooldown, and a website with more pages than the crawler will scan is deactivated. An inactive website only has
    its critical pages checked.

    Args:
        client: The HTTP client for making web requests.
        website: The website to scan.
        max_pages: The most pages to crawl, or None for the default.
        delay: Seconds to wait between requests, or None for the website's own.
        concurrent: The most requests at once, or None for the website's own.
        init: Re-save the website's current state as its baseline. Not needed for new websites, which are
            baselined automatically.

    Returns:
        The HTML report if changes were found or the scan ran into a problem, otherwise None.

    Raises:
        TrafficError: If the website rate limited a scan that was given its own delay or concurrency.
        ScanAlreadyQueuedError: If the website is already queued or being scanned.
        ScanCancelledError: If the scan was cancelled before it finished.
    """
    try:
        website_updates: WebsiteUpdate | None = await scan_queue.run(
            str(website.url), lambda: _find_updates(client, website, max_pages, delay, concurrent, init)
        )
    except TrafficError as error:
        logger.error("Temporary ban or severe rate limit detected for %s: %s", website.url, error)
        if delay or concurrent:
            raise TrafficError(
                url=str(website.url),
                status_code=error.status_code,
                message="Scan aborted, try increasing delay or reducing concurrent (may be banned)",
            ) from error
        return _handle_scan_failure(
            website, ScanStatus.TRAFFIC_ERROR, lambda websites: websites.handle_traffic_error(website)
        )
    except WebConnectionError as error:
        logger.error("Site unreachable: %s", error)
        return _handle_scan_failure(
            website, ScanStatus.CONNECTION_ERROR, lambda websites: websites.handle_connection_error(website.id)
        )
    except WebsiteTooLargeError as error:
        logger.warning("Website too large to scan: %s", error)
        return await _deactivate_then_check_critical_pages(client, website, error.max_pages, init)

    saved_website: WebsiteRead = _save_updates(website, website_updates)
    if not (website_updates and website_updates.has_changes):
        logger.info("No changes found for %s", website.url)
        return None
    return generate_scan_report_html(_only_pages_changed_by(website_updates, saved_website), status=ScanStatus.SUCCESS)
