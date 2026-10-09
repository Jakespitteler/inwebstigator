import asyncio
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from httpx2 import AsyncClient

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.scanning.scan_queue import scan_queue
from app.backend.scanning.scan_reports import WebsiteReport, send_reports_awaiting_email, write_report
from app.backend.scanning.website_scan import record_scan, scan_website
from app.core.config import config
from app.core.errors import NotFoundError, ScanAlreadyQueuedError, ScanCancelledError
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.scan_run_models import ScanRunRead, ScanStatus
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)

SCAN_FAILED_MESSAGE: str = (
    "The scan failed unexpectedly, so changes since the last scan have not been checked. "
    "It will be tried again at the next scheduled scan."
)


@dataclass
class RunAllScans:
    """A "Run All Scans" started from the dashboard, so it can be cancelled part way through.

    Attributes:
        cancelled: Whether it has been cancelled, so no more websites are scanned.
        website_url: The website being scanned now, so its scan can be cancelled too.
    """

    cancelled: bool = False
    website_url: str | None = None


# The dashboard's "Run All Scans" in progress, if there is one
current_run_all: RunAllScans | None = None


def cancel_run_all() -> bool:
    """Cancels the dashboard's "Run All Scans": the website being scanned is cancelled and the rest are skipped.

    Websites already scanned keep their results, and their changes are still emailed.

    Returns:
        True if it was cancelled, or False if no "Run All Scans" was running.
    """
    run_all: RunAllScans | None = current_run_all
    if run_all is None or run_all.cancelled:
        return False

    run_all.cancelled = True
    if run_all.website_url:
        scan_queue.cancel(run_all.website_url)
    return True


def run_all_in_progress() -> bool:
    """Checks whether the dashboard's "Run All Scans" is running, so the dashboard can show its Cancel button."""
    return current_run_all is not None


def is_due_a_scan(website: WebsiteRead, run_started_at: datetime) -> bool:
    """Checks whether enough time has passed since a website's last scan for it to be scanned again.

    Time is counted from the exact time of the last scan, less a small tolerance, so a run that starts a few
    seconds earlier than the last one did does not put the scan off until the next run. (Counting from the
    start of the day would round intervals such as 1.5 days down to whole days.) The tolerance is never more
    than half the time between runs, so short intervals are scanned at the run nearest to when they are due.

    Args:
        website: The website to check.
        run_started_at: When the current run started.

    Returns:
        True if the website has never been scanned or its interval has (nearly) passed, otherwise False.
    """
    if website.last_scan_at is None:
        return True
    interval: timedelta = timedelta(days=website.days_between_scans)
    tolerance: timedelta = min(
        timedelta(minutes=config.scheduler_scan_due_tolerance_minutes),
        timedelta(days=config.scheduler_minimum_days_between_scans) / 2,
    )
    return run_started_at - website.last_scan_at >= interval - tolerance


def _latest_state(website: WebsiteRead) -> WebsiteRead | None:
    """Re-reads a website from the database.

    Args:
        website: The website as it was when the run started.

    Returns:
        The website as it is now, or None if it has since been deleted.
    """
    with db_context() as session:
        try:
            return WebsiteService(session).get(website.id)
        except NotFoundError:
            return None


def _websites_to_scan(
    listed_websites: Iterable[WebsiteRead], run_started_at: datetime, ignore_schedule: bool
) -> Iterator[WebsiteRead]:
    """Yields each website that should be scanned in this run, skipping (and logging) the rest.

    A scan of every website can take hours, so each website is only re-read when the run asks for the next one,
    just before its turn. That picks up changes made since the run started (e.g. it being deleted, deactivated or
    put on cooldown). A website that cannot be read is skipped, so one problem cannot stop the run.

    Args:
        listed_websites: Every website, as it was when the run started.
        run_started_at: When the run started.
        ignore_schedule: Also scan websites that are not due a scan yet. Websites on cooldown are still skipped.

    Yields:
        Each website to scan, as it is just before its turn.
    """
    for listed_website in listed_websites:
        try:
            website: WebsiteRead | None = _latest_state(listed_website)
        except Exception:
            logger.exception("%s has been skipped as it could not be read from the database.", listed_website.url)
            continue
        if website is None:
            logger.info("%s has been skipped as it was deleted during the run.", listed_website.url)
            continue
        if website.on_cooldown_until and website.on_cooldown_until > datetime.now(UTC):
            logger.warning("%s has been skipped as it is on cooldown.", website.url)
            continue
        if not ignore_schedule and not is_due_a_scan(website, run_started_at):
            logger.info("%s has been skipped as there has not been enough time since last scan.", website.url)
            continue
        yield website


def _record_scan_time(website: WebsiteRead, run_started_at: datetime) -> None:
    """Records when a website was scanned, so it is not scanned again until it is next due.

    A failure is logged rather than raised, so the reports already found are still emailed.

    Args:
        website: The website that was scanned.
        run_started_at: When the run started, which is recorded as the time of the scan.
    """
    try:
        with db_context() as session:
            WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=run_started_at))
    except Exception:
        logger.exception("Could not record when %s was scanned, so it may be scanned again early.", website.url)


def _record_failed_scan(website: WebsiteRead) -> ScanRunRead | None:
    """Records a scan that failed unexpectedly, so the website's recipients are told it failed rather than being left
    to think nothing changed.

    A failure to record it is logged rather than raised, so the other websites are still scanned.

    Args:
        website: The website whose scan failed.

    Returns:
        The recorded scan, or None if it could not be recorded.
    """
    try:
        with db_context() as session:
            scan_run: ScanRunRead = record_scan(session, website, ScanStatus.SCAN_ERROR, SCAN_FAILED_MESSAGE)
    except Exception:
        logger.exception("Could not record that the scan of %s failed.", website.url)
        return None
    return scan_run


async def scan_all_websites(
    ignore_schedule: bool = False, email_sender: EmailSender | None = None, run_all: RunAllScans | None = None
) -> str | None:
    """Scans every website that is due a scan, then emails each recipient one email with the reports for all of
    their websites. Inactive websites only have their critical pages checked, and websites on cooldown are skipped.

    Every report that has not been emailed yet is sent, including reports from earlier scans whose email failed
    (e.g. because the mail server was down), so changes are not lost. The emails are sent from a thread, so the
    dashboard and other scans keep running while the mail server is slow.

    A website whose scan fails unexpectedly is logged and its recipients are sent a failure report, a
    website that cannot be read or updated in the database is logged and skipped, and a failed email is
    logged and kept to send again next time, so one problem cannot stop the other websites being scanned or
    reported. A website deleted during the run is skipped, or its scan cancelled if it was being scanned.

    If `run_all` is cancelled, no more websites are scanned, but the changes already found are still emailed.

    Args:
        ignore_schedule: Also scan websites that are not due a scan yet, e.g. for "Run All Scans" on the
            dashboard. Websites on cooldown are still skipped. Defaults to False.
        email_sender: Sends the reports. Defaults to the configured mail server.
        run_all: Lets the dashboard cancel the run. Defaults to None.

    Returns:
        The reports of this run's scans as HTML if any changes were found, otherwise None.
    """
    with db_context() as session:
        listed_websites: Sequence[WebsiteRead] = WebsiteService(session).get_all(limit=None)

    run_started_at: datetime = datetime.now(UTC)
    reports: list[WebsiteReport] = []
    async with AsyncClient() as client:
        for website in _websites_to_scan(listed_websites, run_started_at, ignore_schedule):
            if run_all and run_all.cancelled:
                logger.info("Run All Scans was cancelled, so the remaining websites have been skipped.")
                break

            if run_all:
                run_all.website_url = str(website.url)
            try:
                scan_run: ScanRunRead | None = await scan_website(client, website)
            except (ScanAlreadyQueuedError, ScanCancelledError) as error:
                logger.info("A website has been skipped: %s", error)
                continue
            except Exception:
                logger.exception("Scan failed for %s, continuing with the remaining websites.", website.url)
                scan_run = _record_failed_scan(website)
            finally:
                if run_all:
                    run_all.website_url = None

            if scan_run and scan_run.has_report:
                reports.append(write_report(website, scan_run))
            _record_scan_time(website, run_started_at)

    await asyncio.to_thread(send_reports_awaiting_email, email_sender or get_email_sender())
    return "".join(report.html for report in reports) or None


async def scan_all_websites_now(email_sender: EmailSender | None = None) -> str | None:
    """Scans every website now for the dashboard's "Run All Scans", in a way `cancel_run_all()` can stop.

    Websites not due a scan yet are included, while websites on cooldown are still skipped.

    Args:
        email_sender: Sends the reports. Defaults to the configured mail server.

    Returns:
        The reports of this run's scans as HTML if any changes were found, otherwise None.
    """
    global current_run_all

    run_all = RunAllScans()
    current_run_all = run_all
    try:
        return await scan_all_websites(ignore_schedule=True, email_sender=email_sender, run_all=run_all)
    finally:
        current_run_all = None
