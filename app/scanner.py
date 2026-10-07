import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import NamedTuple

from httpx2 import AsyncClient

from app.backend.change_detection import get_critical_page_only_updates, get_website_updates
from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.email_service.email_wording import scan_report_subject
from app.backend.email_service.html_bodies import ScanStatus, generate_scan_report_html, join_scan_reports
from app.backend.email_service.message_builder import OutgoingEmail
from app.core.config import config
from app.core.errors import (
    NotFoundError,
    ScanAlreadyQueuedError,
    ScanCancelledError,
    TrafficError,
    WebConnectionError,
    WebsiteTooLargeError,
)
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.critical_page_models import CriticalPageRead
from app.models.field_types import EmailString
from app.models.recipient_models import RecipientUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

queued_crawls: dict[str, asyncio.Task[WebsiteUpdate | None]] = {}
scan_lock: asyncio.Lock = asyncio.Lock()


class WebsiteReport(NamedTuple):
    """One website's scan report, ready to be emailed to its recipients.

    Attributes:
        website_url: The URL of the website the report is about, used to name it in the subject line.
        html: The report card as HTML.
    """

    website_url: str
    html: str


def _record_email_sent(recipient_email: EmailString) -> None:
    """Records that a recipient was just emailed, so their health checks count from now.

    Args:
        recipient_email (EmailString): The recipient's email address.
    """
    with db_context() as session:
        recipient_service = RecipientService(session)
        recipient = recipient_service.get_by_email(recipient_email)
        recipient_service.update(id=recipient.id, model_update=RecipientUpdate(last_email_at=datetime.now()))


def send_notification(recipient_email: EmailString, html_body: str, subject: str, email_sender: EmailSender) -> None:
    """Emails a recipient, then records when they were emailed.

    The email has already gone by the time it is recorded, so a failure to record it is logged rather than
    raised, and is not mistaken for the email failing to send.

    Args:
        recipient_email (EmailString): The recipient's email address.
        html_body (str): The email's content as HTML.
        subject (str): The email's subject line.
        email_sender (EmailSender): Sends the email.

    Raises:
        smtplib.SMTPException: If the email could not be sent.
        OSError: If the mail server could not be reached.
    """
    email_sender.send(OutgoingEmail(to=recipient_email, subject=subject, html_body=html_body))
    try:
        _record_email_sent(recipient_email)
    except Exception:
        logger.exception(
            "%s was emailed, but when could not be recorded, so a health check may come early.", recipient_email
        )


def send_report_to_recipients(
    recipient_emails: Sequence[EmailString],
    html_body: str,
    subject: str,
    email_sender: EmailSender,
) -> None:
    """Emails the same report to each recipient, over one connection to the mail server.

    Blocks while the emails send, so async code runs it in a thread.

    Args:
        recipient_emails (Sequence[EmailString]): Who to email.
        html_body (str): The report as HTML.
        subject (str): The email's subject line.
        email_sender (EmailSender): Sends the emails.

    Raises:
        smtplib.SMTPException: If an email could not be sent. The recipients after it are not emailed.
        OSError: If the mail server could not be reached.
    """
    with email_sender:
        for recipient_email in recipient_emails:
            send_notification(recipient_email, html_body, subject, email_sender)


def _email_scan_reports(
    reports_by_recipient: Mapping[EmailString, Sequence[WebsiteReport]],
    email_sender: EmailSender,
) -> None:
    """Emails each recipient one email holding the reports for all of their websites, over one connection.

    A failed email is logged, so one bad address does not stop the other recipients being emailed. Blocks while
    the emails send, so async code runs it in a thread.

    Args:
        reports_by_recipient (Mapping[EmailString, Sequence[WebsiteReport]]): Each recipient's reports.
        email_sender (EmailSender): Sends the emails.
    """
    with email_sender:
        for recipient_email, reports in reports_by_recipient.items():
            try:
                send_notification(
                    recipient_email,
                    html_body=f"<ul>{join_scan_reports(report.html for report in reports)}</ul>",
                    subject=scan_report_subject([report.website_url for report in reports]),
                    email_sender=email_sender,
                )
            except Exception:
                logger.exception("Failed to send scan report to %s", recipient_email)


async def _crawl_in_turn(crawl: Callable[[], Awaitable[WebsiteUpdate | None]]) -> WebsiteUpdate | None:
    """Waits for the scans requested before this one to finish, then crawls the website.

    Every scan waits on the same `scan_lock`, so websites are scanned one at a time, in the order requested.
    """
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


async def _check_for_updates(
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
        client (AsyncClient): The HTTPX asynchronous client for making web requests.
        website (WebsiteRead): The website to check.
        max_pages (int | None): The maximum number of pages to crawl, or None for the default.
        delay (float | None): Time in seconds to wait between requests, or None for the website's own.
        concurrent (int | None): Maximum number of concurrent connections, or None for the website's own.
        init (bool): Re-save the website's current state as its baseline.

    Returns:
        WebsiteUpdate | None: The updates found, or None if nothing changed.
    """
    if not website.active:
        return await get_critical_page_only_updates(client, website, init)

    with db_context() as session:
        stored_internal_links: list[str] = InternalLinkService(session).get_urls_for_website(website.id)
    return await get_website_updates(client, website, stored_internal_links, max_pages, delay, concurrent, init)


async def _handle_too_large_website(client: AsyncClient, website: WebsiteRead, max_pages: int, init: bool) -> str:
    """Deactivates a website with more pages than the crawler will scan, then checks its critical pages,
    which are still watched while it is inactive, rather than leaving them until its next scan.

    Args:
        client (AsyncClient): The HTTPX asynchronous client for making web requests.
        website (WebsiteRead): The website found to be too large.
        max_pages (int): The most pages the crawler would scan.
        init (bool): Re-save the critical pages' current state as their baseline.

    Returns:
        str: The HTML report saying the website was deactivated, followed by any changes found on its critical pages.

    Raises:
        ScanCancelledError: If the check of the critical pages was cancelled before it finished.
    """
    with db_context() as session:
        website_service = WebsiteService(session)
        action_message: str = website_service.handle_too_large(website.id, max_pages)
        inactive_website: WebsiteRead = website_service.get(website.id)

    too_large_report: str = generate_scan_report_html(website, status=ScanStatus.TOO_LARGE, message=action_message)
    # The website is now inactive, so this scan only checks its critical pages and cannot find it too large again
    critical_page_report: str | None = await scan_website(client, inactive_website, init=init)
    return join_scan_reports(report for report in (too_large_report, critical_page_report) if report)


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
    cooldown periods to the affected website record, and deactivates websites with
    more pages than the crawler will scan. An inactive website only has its critical pages
    checked. Waits for any scan already running to finish first.

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
            website.url, lambda: _check_for_updates(client, website, max_pages, delay, concurrent, init)
        )
    except TrafficError as e:
        logger.error("Temporary ban or severe rate limit detected for %s: %s", website.url, e)
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
        logger.error("Site unreachable: %s", e)
        with db_context() as session:
            action_message: str = WebsiteService(session).handle_connection_error(website.id)
        return generate_scan_report_html(website, status=ScanStatus.CONNECTION_ERROR, message=action_message)
    except WebsiteTooLargeError as e:
        logger.warning("Website too large to scan: %s", e)
        return await _handle_too_large_website(client, website, e.max_pages, init)

    with db_context() as session:
        website_service = WebsiteService(session)
        if website_updates:
            website = website_service.update(id=website.id, model_update=website_updates)
        website_service.reset_failed_attempts(website.id)

    if not (website_updates and website_updates.has_changes):
        logger.info("No changes found for %s", website.url)
        return None

    # Unchanged pages keep the recent changes from an earlier scan, so only pages changed by this scan are reported
    changed_pages: list[CriticalPageRead] = [
        page for page in website.critical_pages if page.id in website_updates.changed_page_ids
    ]
    return generate_scan_report_html(
        website.model_copy(update={"critical_pages": changed_pages}),
        status=ScanStatus.SUCCESS,
    )


def _get_latest_state(website: WebsiteRead) -> WebsiteRead | None:
    """Gets the latest saved state of a website, or None if it has been deleted.

    A scan of every website can take hours, so each website is re-read just before its turn
    to pick up changes made since the run started (e.g. it being deleted or deactivated).

    Args:
        website (WebsiteRead): The website as it was when the run started.

    Returns:
        WebsiteRead | None: The website as it is now, or None if it has since been deleted.
    """
    with db_context() as session:
        try:
            return WebsiteService(session).get(website.id)
        except NotFoundError:
            return None


def _is_due_a_scan(website: WebsiteRead, run_started_at: datetime) -> bool:
    """Checks whether enough time has passed since a website's last scan for it to be scanned again.

    Time is counted from the exact time of the last scan, less a small tolerance, so a run that starts a few
    seconds earlier than the last one did does not put the scan off until the next run. (Counting from the
    start of the day would round intervals such as 1.5 days down to whole days.) The tolerance is never more
    than half the time between runs, so short intervals are scanned at the run nearest to when they are due.

    Args:
        website (WebsiteRead): The website to check.
        run_started_at (datetime): When the current run started.

    Returns:
        bool: True if the website has never been scanned or its interval has (nearly) passed, otherwise False.
    """
    if website.last_scan_at is None:
        return True
    interval: timedelta = timedelta(days=website.days_between_scans)
    tolerance: timedelta = min(
        timedelta(minutes=config.scheduler_scan_due_tolerance_minutes),
        timedelta(days=config.scheduler_minimum_days_between_scans) / 2,
    )
    return run_started_at - website.last_scan_at >= interval - tolerance


async def scan_all_websites(ignore_schedule: bool = False, email_sender: EmailSender | None = None) -> str | None:
    """Asynchronously scans all non-cooldown websites that are due a scan. Inactive websites
    only have their critical pages checked.

    Updates the recipient's `last_scan_at` metadata and dispatches an HTML email notification
    if any scan reports were generated. The emails are sent from a thread, so the dashboard and other
    scans keep running while the mail server is slow.

    A website whose scan fails unexpectedly is logged and its recipients are sent a failure report, a
    website that cannot be read or updated in the database is logged and skipped, and a failed email is
    logged and skipped, so one problem cannot stop the other websites being scanned or reported. A website
    deleted during the run is skipped, or its scan cancelled if it was being scanned.

    Args:
        ignore_schedule (bool, optional): Also scan websites that are not due a scan yet, e.g. for
            "Run All Scans" on the dashboard. Websites on cooldown are still skipped. Defaults to False.
        email_sender (EmailSender | None, optional): Sends the reports. Defaults to the configured mail server.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates/errors occurred,
        otherwise None.
    """

    with db_context() as session:
        website_service = WebsiteService(session)
        websites: Sequence[WebsiteRead] = website_service.get_all(limit=None)  # Every website, not just the first 100

    run_started_at = datetime.now()
    reports_by_recipient: dict[EmailString, list[WebsiteReport]] = defaultdict(list)
    all_reports: list[str] = []
    async with AsyncClient() as client:
        for listed_website in websites:
            try:
                website: WebsiteRead | None = _get_latest_state(listed_website)
            except Exception:
                logger.exception("%s has been skipped as it could not be read from the database.", listed_website.url)
                continue
            if website is None:
                logger.info("%s has been skipped as it was deleted during the run.", listed_website.url)
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                logger.warning("%s has been skipped as it is on cooldown.", website.url)
                continue
            if not ignore_schedule and not _is_due_a_scan(website, run_started_at):
                logger.info("%s has been skipped as there has not been enough time since last scan.", website.url)
                continue

            try:
                report: str | None = await scan_website(client, website)
            except ScanAlreadyQueuedError:
                logger.info("%s has been skipped as it is already queued or being scanned.", website.url)
                continue
            except ScanCancelledError:
                logger.info("%s has been skipped as its scan was cancelled.", website.url)
                continue
            except Exception:
                logger.exception("Scan failed for %s, continuing with the remaining websites.", website.url)
                # Tell the recipients, rather than leaving them to think nothing has changed
                report = generate_scan_report_html(
                    website,
                    status=ScanStatus.SCAN_ERROR,
                    message="The scan failed unexpectedly, so changes since the last scan have not been checked. "
                    "It will be tried again at the next scheduled scan.",
                )

            if report:
                all_reports.append(report)
                for recipient in website.recipients:
                    reports_by_recipient[recipient.email].append(WebsiteReport(website.url, report))
            else:
                logger.info("No updates found for: %s", website.url)

            # A failure here is logged rather than raised, so the reports already found are still emailed
            try:
                with db_context() as session:
                    WebsiteService(session).update(
                        id=website.id, model_update=WebsiteUpdate(last_scan_at=run_started_at)
                    )
            except Exception:
                logger.exception("Could not record when %s was scanned, so it may be scanned again early.", website.url)

    await asyncio.to_thread(_email_scan_reports, reports_by_recipient, email_sender or get_email_sender())

    return "".join(all_reports) or None
