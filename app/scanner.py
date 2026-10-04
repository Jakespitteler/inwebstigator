import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, time, timedelta

from httpx2 import AsyncClient

from app.backend.email_service import build_message, send_email
from app.backend.engine import get_website_updates
from app.backend.format_message import ScanStatus, generate_scan_report_html, monitoring_started_html
from app.core.errors import ScanAlreadyQueuedError, ScanCancelledError, TrafficError, WebConnectionError
from app.db.core import db_context
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.utils.field_types import EmailString
from app.models.critical_page_models import CriticalPageRead
from app.models.recipient_models import RecipientUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

# Websites are scanned one at a time, in the order requested, so a new scan waits for the current one to finish
scan_lock = asyncio.Lock()

# The crawl of each website that is queued or being scanned, by URL, so it can be cancelled
queued_crawls: dict[str, asyncio.Task[WebsiteUpdate | None]] = {}


def send_notification(recipient_email: EmailString, report: str, subject: str):
    """Builds and sends an HTML email notification containing a website scan report
    and updates the recipient's `last_email_at` timestamp.

    Args:
        recipient (RecipientRead): The target recipient recipient.
        report (str): The HTML formatted scan report to include in the email body.
        subject (str, optional): The email subject line.
    """

    send_email(msg=build_message(subject=subject, recipients=[recipient_email], html_body=report))

    with db_context() as session:
        recipient_service = RecipientService(session)
        recipient = recipient_service.get_by_email(recipient_email)
        recipient_service.update(id=recipient.id, model_update=RecipientUpdate(last_email_at=datetime.now()))


def send_monitoring_started_notifications(website: WebsiteRead) -> None:
    """Emails each of a website's recipients to confirm the website is now being monitored.

    A failed send is logged and skipped, so it cannot fail the request that added the website
    or stop the remaining recipients being emailed.

    Args:
        website (WebsiteRead): The website that has started being monitored.
    """
    for recipient in website.recipients:
        try:
            send_notification(
                recipient_email=recipient.email,
                report=monitoring_started_html(website, recipient.days_between_health_checks),
                subject="Website monitoring started",
            )
        except Exception:
            logger.exception(f"Failed to send monitoring started email to {recipient.email}")


async def _crawl_in_turn(crawl: Callable[[], Awaitable[WebsiteUpdate | None]]) -> WebsiteUpdate | None:
    """Waits for the scans requested before this one to finish, then crawls the website."""
    async with scan_lock:
        return await crawl()


async def queued_crawl(url: str, crawl: Callable[[], Awaitable[WebsiteUpdate | None]]) -> WebsiteUpdate | None:
    """Crawls a website once the scans requested before it have finished.

    The crawl runs as its own task, so `cancel_scan()` can stop it whether it is waiting its turn or
    already crawling, without stopping whatever requested it (e.g. a scan of all websites).

    Args:
        url (str): The URL of the website to crawl.
        crawl (Callable[[], Awaitable[WebsiteUpdate | None]]): Starts the crawl once it is this website's turn.

    Returns:
        WebsiteUpdate | None: The updates found by the crawl.

    Raises:
        ScanAlreadyQueuedError: If the website is already queued or being scanned.
        ScanCancelledError: If the scan was cancelled before it finished.
    """
    if url in queued_crawls:
        raise ScanAlreadyQueuedError(url)

    crawl_task = asyncio.create_task(_crawl_in_turn(crawl))
    queued_crawls[url] = crawl_task
    try:
        return await crawl_task
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task and current_task.cancelling():
            raise  # The app is shutting down, rather than the scan being cancelled
        raise ScanCancelledError(url) from None
    finally:
        del queued_crawls[url]


def cancel_scan(url: str) -> bool:
    """Cancels a website's scan, whether it is waiting its turn or already crawling.

    Nothing found by a cancelled scan is saved.

    Args:
        url (str): The URL of the website whose scan to cancel.

    Returns:
        bool: True if the scan was cancelled, or False if the website was not queued or being scanned.
    """
    crawl_task = queued_crawls.get(url)
    return crawl_task is not None and crawl_task.cancel()


async def scan_website(
    client: AsyncClient,
    website: WebsiteRead,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
    init: bool = False,
) -> str | None:
    """Asynchronously scans a website for updates, updates the database record, and
    generates an HTML scan report.

    Automatically handles rate limits and unreachable sites by applying database
    cooldown periods to the affected website record. Waits for any scan already
    running to finish first.

    Args:
        client (AsyncClient): The HTTPX asynchronous client for making web requests.
        website (WebsiteRead): The website database record to scan.
        max_pages (int | None, optional): The maximum number of pages to crawl. Defaults to None.
        delay (float | None, optional): Time in seconds to wait between requests. Defaults to None.
        concurrent (int | None, optional): Maximum number of concurrent connections. Defaults to None.
        init (bool, optional): Re-save the website's current state as its baseline. Not needed for
            new websites, which are baseline automatically. Defaults to False.

    Returns:
        str | None: An HTML scan report string if updates or errors were recorded,
        otherwise None if no changes were found.

    Raises:
        TrafficError: Re-raised if custom delay/concurrent parameters were set during a rate-limited scan.
        ScanAlreadyQueuedError: If the website is already queued or being scanned.
        ScanCancelledError: If the scan was cancelled before it finished.
    """
    try:
        website_updates: WebsiteUpdate | None = await queued_crawl(
            website.url, lambda: get_website_updates(client, website, max_pages, delay, concurrent, init)
        )
    except TrafficError as e:
        logger.error(f"Temporary ban or severe rate limit detected for {website.url}: {e}")
        if delay or concurrent:
            raise TrafficError(
                url=website.url,
                status_code=e.status_code,
                message="Scan aborted, try increasing delay or reducing concurrent (may be banned)",
            ) from e
        with db_context() as session:
            action_message: str = WebsiteService(session).handle_traffic_error(website)
        return generate_scan_report_html(website, status=ScanStatus.TRAFFIC_ERROR, message=action_message)
    except WebConnectionError as e:
        logger.error(f"Site unreachable: {e}")
        with db_context() as session:
            action_message: str = WebsiteService(session).handle_connection_error(website.id)
        return generate_scan_report_html(website, status=ScanStatus.CONNECTION_ERROR, message=action_message)

    with db_context() as session:
        website_service = WebsiteService(session)
        if website_updates:
            website = website_service.update(id=website.id, model_update=website_updates)
        website_service.reset_failed_attempts(website.id)

    if not (website_updates and website_updates.has_changes):
        logger.info(f"No changes found for {website.url}")
        return None

    # Unchanged pages keep the recent changes from an earlier scan, so only pages changed by this scan are reported
    changed_pages: list[CriticalPageRead] = [
        page for page in website.critical_pages if page.id in website_updates.changed_page_ids
    ]
    return generate_scan_report_html(
        website.model_copy(update={"critical_pages": changed_pages}),
        status=ScanStatus.SUCCESS,
    )


async def scan_all_websites() -> str | None:
    """Asynchronously scans all active, non-cooldown websites registered to a recipient.

    Updates the recipient's `last_scan_at` metadata and dispatches an HTML email notification
    if any scan reports were generated.

    A website whose scan fails unexpectedly is logged and skipped, and a failed email is logged
    and skipped, so one problem cannot stop the other websites being scanned or reported.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates/errors occurred,
        otherwise None.
    """

    with db_context() as session:
        website_service = WebsiteService(session)
        websites: Sequence[WebsiteRead] = website_service.get_all()

    run_started_at = datetime.now()
    reports_by_recipient: dict[EmailString, list[str]] = defaultdict(list)
    all_reports: list[str] = []
    async with AsyncClient() as client:
        for website in websites:
            if not website.active:
                logger.warning(f"{website.url} has been skipped as it has been deactivated.")
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                logger.warning(f"{website.url} has been skipped as it is on cooldown.")
                continue
            if website.last_scan_at and (
                run_started_at - datetime.combine(website.last_scan_at.date(), time.min)  # Start of the day
            ) < timedelta(days=website.days_between_scans):
                logger.info(f"{website.url} has been skipped as there has not been enough time since last scan.")
                continue

            try:
                report: str | None = await scan_website(client, website)
            except ScanAlreadyQueuedError:
                logger.info(f"{website.url} has been skipped as it is already queued or being scanned.")
                continue
            except ScanCancelledError:
                logger.info(f"{website.url} has been skipped as its scan was cancelled.")
                continue
            except Exception:
                logger.exception(f"Scan failed for {website.url}, continuing with the remaining websites.")
                report = None

            if report:
                all_reports.append(report)
                for recipient in website.recipients:
                    reports_by_recipient[recipient.email].append(report)
            else:
                logger.info(f"No updates found for: {website.url}")

            with db_context() as session:
                WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=run_started_at))

    for recipient_email, recipient_reports in reports_by_recipient.items():
        try:
            send_notification(
                recipient_email,
                report=f"<ul>{''.join(recipient_reports)}</ul>",
                subject="Website Update",
            )
        except Exception:
            logger.exception(f"Failed to send scan report to {recipient_email}")

    return "".join(all_reports) or None
