import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.email_service.email_wording import WebsiteHealth, describe_website_health, health_check_subject
from app.backend.email_service.html_bodies import health_check_html
from app.core.config import config
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.session import get_db_session
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteRead
from app.scanner import scan_all_websites, send_notification

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
SCAN_JOB_ID: str = "scan_then_send_health_checks"
db_context = contextmanager(get_db_session)


def _is_health_check_due(recipient: RecipientRead, now: datetime) -> bool:
    """Checks whether a recipient has gone long enough without an email to be sent a health check.

    The threshold counts from the start of the day they were last emailed. Emails go out partway
    through a daily run, so counting from the exact time would make the next due run look slightly
    short of the threshold and delay the health check by a whole day.

    Args:
        recipient (RecipientRead): The recipient to check.
        now (datetime): The time of the check.

    Returns:
        bool: True if the recipient has been emailed before and the threshold has passed since.
    """
    if recipient.last_email_at is None:
        return False
    start_of_last_email_day: datetime = datetime.combine(recipient.last_email_at.date(), time.min)
    return now - start_of_last_email_day >= timedelta(days=recipient.days_between_health_checks)


def _websites_by_recipient(websites: Sequence[WebsiteRead]) -> dict[str, list[WebsiteRead]]:
    """Groups websites by the email address of each of their recipients.

    Args:
        websites (Sequence[WebsiteRead]): Every website.

    Returns:
        dict[str, list[WebsiteRead]]: The websites each recipient is emailed about.
    """
    websites_by_recipient: defaultdict[str, list[WebsiteRead]] = defaultdict(list)
    for website in websites:
        for recipient in website.recipients:
            websites_by_recipient[recipient.email].append(website)
    return websites_by_recipient


def _send_health_check_if_no_change(
    recipient: RecipientRead,
    websites: Sequence[WebsiteRead],
    email_sender: EmailSender,
) -> None:
    """Dispatches a health check notification to a recipient if no email notification
    has been sent within the designated health check threshold.

    The health check lists how each of the recipient's websites is doing, so it does not say all is well
    while scans are failing or a website is switched off.

    Args:
        recipient (RecipientRead): The target recipient to check and notify.
        websites (Sequence[WebsiteRead]): The recipient's websites, as saved after the latest scan.
        email_sender (EmailSender): Sends the email.
    """
    now: datetime = datetime.now()
    if not _is_health_check_due(recipient, now):
        return
    website_healths: list[WebsiteHealth] = [describe_website_health(website, now) for website in websites]
    send_notification(
        recipient.email,
        html_body=health_check_html(website_healths),
        subject=health_check_subject(website_healths),
        email_sender=email_sender,
    )


def _send_health_checks(
    recipients: Sequence[RecipientRead],
    websites_by_recipient: Mapping[str, Sequence[WebsiteRead]],
    email_sender: EmailSender,
) -> None:
    """Sends the health checks that are due, over one connection to the mail server.

    A failed email is logged, so one bad address does not stop the other recipients' health checks. Blocks
    while the emails send, so it is run in a thread.

    Args:
        recipients (Sequence[RecipientRead]): Every recipient still linked to a website.
        websites_by_recipient (Mapping[str, Sequence[WebsiteRead]]): Each recipient's websites, by email address.
        email_sender (EmailSender): Sends the emails.
    """
    with email_sender:
        for recipient in recipients:
            try:
                _send_health_check_if_no_change(recipient, websites_by_recipient.get(recipient.email, []), email_sender)
            except Exception:
                logger.exception("Failed to send health check to %s", recipient.email)


async def _scan_then_send_health_checks(email_sender: EmailSender | None = None) -> None:
    """Scans the websites that are due, then sends the health checks that are due.

    Recipients are read after the scan, on every run, so a change notification sent by the scan
    counts as recent contact, and newly added recipients and changed intervals are picked up.
    Only recipients still linked to a website are sent health checks.

    Args:
        email_sender (EmailSender | None, optional): Sends the emails. Defaults to the configured mail server.
    """
    sender: EmailSender = email_sender or get_email_sender()
    await scan_all_websites(email_sender=sender)

    with db_context() as session:
        recipients: Sequence[RecipientRead] = RecipientService(session).get_all_with_websites()
        websites: Sequence[WebsiteRead] = WebsiteService(session).get_all(limit=None)
    await asyncio.to_thread(_send_health_checks, recipients, _websites_by_recipient(websites), sender)


def next_scheduled_check() -> datetime | None:
    """Gets when the scheduler next checks which websites are due a scan.

    Returns:
        datetime | None: The time of the next check, or None if automatic scans are not running.
    """
    job = scheduler.get_job(SCAN_JOB_ID)  # type: ignore
    return job.next_run_time if job else None  # type: ignore


def restart_scan_countdown() -> None:
    """Restarts the countdown to the next scheduled check, e.g. after every website has just been scanned on demand.

    Does nothing if automatic scans are not running.
    """
    if scheduler.get_job(SCAN_JOB_ID) is None:  # pyright: ignore[reportUnknownMemberType]
        return
    # A new interval trigger first fires one interval from now
    scheduler.reschedule_job(  # pyright: ignore[reportUnknownMemberType]
        job_id=SCAN_JOB_ID,
        trigger="interval",
        days=config.scheduler_minimum_days_between_scans,
    )


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan context manager that schedules the recurring website scans and health
    checks, and manages the APScheduler lifecycle.

    Args:
        app (FastAPI): The application instance.

    Yields:
        None: Yields control back to FastAPI while the scheduler is active.
    """

    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        func=_scan_then_send_health_checks,
        trigger="interval",
        days=config.scheduler_minimum_days_between_scans,
        next_run_time=datetime.now() + timedelta(seconds=1),
        misfire_grace_time=None,
        id=SCAN_JOB_ID,
        replace_existing=True,
    )

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
