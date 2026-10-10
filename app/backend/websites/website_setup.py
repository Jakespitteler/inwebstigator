import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime

from pydantic import HttpUrl

from app.backend.crawler.page_fetcher import new_http_client
from app.backend.email_service.delivery import EmailSender
from app.backend.email_service.html_bodies import monitoring_started_html, recipient_added_html
from app.backend.scanning.change_detection import get_critical_page_updates
from app.backend.scanning.scan_queue import scan_queue
from app.backend.scanning.website_scan import scan_website
from app.backend.websites.page_checks import check_pages_exist
from app.backend.websites.recipient_checks import confirm_addresses_can_receive_email
from app.core.config import config
from app.core.errors import (
    AlreadyWatchedError,
    InvalidPageError,
    NotFoundError,
    ScanAlreadyQueuedError,
    ScanCancelledError,
    WebsiteAlreadyMonitoredError,
)
from app.core.urls import is_same_page, resolve_critical_page_url
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteSettingsUpdate, WebsiteUpdate

MONITORING_STARTED_SUBJECT: str = "Website monitoring started"
RECIPIENT_ADDED_SUBJECT: str = "Email address added to website monitoring"


def _refuse_website_already_watched(url: HttpUrl) -> None:
    """Stops a website being added twice, which would scan it twice and email every change twice.

    The same website written differently (e.g. with "www.", a trailing "/", or http instead of https) counts as the
    same website, matched the same way as when it is saved, so nobody is emailed about a website that cannot be added.
    A different part of a website (e.g. example.com/research when example.com is watched) can still be added.

    Args:
        url: The URL of the website being added.

    Raises:
        WebsiteAlreadyMonitoredError: If the website is already being watched, naming it as it is saved.
    """
    with db_context() as session:
        try:
            watched_website: WebsiteRead = WebsiteService(session).get_by_url(url)
        except NotFoundError:
            return
    raise WebsiteAlreadyMonitoredError(str(watched_website.url))


def _discard_website(website_id: uuid.UUID) -> None:
    """Deletes a website whose first scan was cancelled, so cancelling the scan cancels adding the website.

    A website that has already been deleted is left alone, as deleting a website also cancels its scan.

    Args:
        website_id: The ID of the website to delete.
    """
    with db_context() as session, suppress(NotFoundError):
        WebsiteService(session).delete(website_id)


def _record_scan_time(website_id: uuid.UUID) -> None:
    """Records that a website was just scanned, so the dashboard and the scheduler see it.

    Args:
        website_id: The ID of the website that was scanned.
    """
    with db_context() as session:
        WebsiteService(session).update(id=website_id, model_update=WebsiteUpdate(last_scan_at=datetime.now(UTC)))


async def add_website(model_create: WebsiteCreate, email_sender: EmailSender) -> WebsiteRead:
    """Adds a website to be monitored, then scans it straight away to save what it looks like now as its baseline.

    Each recipient is sent one email before the scan, saying what is being monitored, which also confirms their
    address can receive email. Other websites can be added while this one is being scanned, and are scanned after it.
    A website too large to scan is kept but deactivated, and only its critical pages are watched.

    Args:
        model_create: The website, its critical pages, its recipients and its scan settings.
        email_sender: Sends the emails to the recipients.

    Returns:
        The website as it was added.

    Raises:
        WebsiteAlreadyMonitoredError: If the website is already being watched. Nothing is added.
        PageNotLoadedError: If the website or one of its critical pages could not be loaded. Nothing is added.
        UndeliverableEmailError: If an email to one of its recipients could not be delivered. Nothing is added.
        ScanCancelledError: If the first scan was cancelled. The website is not kept.
        ScanAlreadyQueuedError: If the same website is already being added or scanned. The website is not kept.
    """
    _refuse_website_already_watched(model_create.url)
    async with new_http_client() as client:
        await check_pages_exist(client, [str(model_create.url), *model_create.critical_pages])
    await confirm_addresses_can_receive_email(
        model_create.recipient_emails,
        subject=MONITORING_STARTED_SUBJECT,
        html_body=monitoring_started_html(model_create, config.scheduler_default_days_between_health_checks),
        email_sender=email_sender,
    )
    with db_context() as session:
        website: WebsiteRead = WebsiteService(session).create(model_create)
        RecipientService(session).record_emailed(model_create.recipient_emails, emailed_at=datetime.now(UTC))

    try:
        async with new_http_client() as client:
            await scan_website(client, website, init=True)
    except (ScanCancelledError, ScanAlreadyQueuedError):
        _discard_website(website.id)
        raise

    _record_scan_time(website.id)
    return website


def _new_critical_page(website: WebsiteRead, page_url: str) -> CriticalPageCreate:
    """Works out the full URL of a critical page being added, checking it is on the website and not already watched.

    Args:
        website: The website the critical page belongs to.
        page_url: The critical page as the user typed it. Can be a full URL, a URL without "https://" or a link
            relative to the website (e.g. "/news/today").

    Returns:
        The critical page to add.

    Raises:
        InvalidPageError: If the page is not a valid URL on the website.
        AlreadyWatchedError: If the page is already being watched, even if written differently (e.g. with a "/").
    """
    try:
        model_create = CriticalPageCreate(
            website_id=website.id, url=HttpUrl(resolve_critical_page_url(str(website.url), page_url))
        )
    except ValueError as error:
        raise InvalidPageError(str(error)) from error

    if any(is_same_page(str(page.url), str(model_create.url)) for page in website.critical_pages):
        raise AlreadyWatchedError(str(model_create.url))
    return model_create


def _discard_critical_page(critical_page_id: uuid.UUID) -> None:
    """Deletes a critical page whose first check failed, so a page that cannot be checked is not added.

    Args:
        critical_page_id: The ID of the critical page to delete.
    """
    with db_context() as session, suppress(NotFoundError):
        CriticalPageService(session).delete(critical_page_id)


async def add_critical_page(website_id: uuid.UUID, page_url: str) -> CriticalPageRead:
    """Adds a critical page to a website, then saves what it looks like now as its baseline.

    Args:
        website_id: The website the critical page belongs to.
        page_url: The critical page as the user typed it. Can be a full URL, a URL without "https://" or a link
            relative to the website (e.g. "/news/today").

    Returns:
        The critical page as it was added.

    Raises:
        NotFoundError: If the website does not exist.
        InvalidPageError: If the page is not a valid URL on the website.
        AlreadyWatchedError: If the page is already being watched.
        PageNotLoadedError: If the page could not be loaded.
    """
    with db_context() as session:
        website: WebsiteRead = WebsiteService(session).get(website_id)
    model_create: CriticalPageCreate = _new_critical_page(website, page_url)

    async with new_http_client() as client:
        await check_pages_exist(client, [str(model_create.url)])
        with db_context() as session:
            critical_page: CriticalPageRead = CriticalPageService(session).create(model_create)
        try:
            baseline: CriticalPageUpdate | None = await get_critical_page_updates(client, critical_page, init=True)
        except Exception:
            _discard_critical_page(critical_page.id)
            raise

    if baseline:
        with db_context() as session:
            CriticalPageService(session).update(id=critical_page.id, model_update=baseline)
    return critical_page


def _not_yet_recipients(website: WebsiteRead, emails: Sequence[str]) -> list[str]:
    """Picks out the email addresses that are not already recipients of a website, each only once.

    Args:
        website: The website.
        emails: The email addresses being added.

    Returns:
        The addresses that are new to the website, in the order given.
    """
    current_emails: set[str] = {recipient.email for recipient in website.recipients}
    return [email for email in dict.fromkeys(emails) if email not in current_emails]


async def update_website_settings(
    website_id: uuid.UUID, settings: WebsiteSettingsUpdate, email_sender: EmailSender
) -> WebsiteRead:
    """Changes a website's settings, first confirming that any recipient being added can receive email.

    Only an address that is not already a recipient of the website is emailed and recorded as emailed, so adding an
    address twice does not tell its owner again that they were added.

    Args:
        website_id: The ID of the website to change.
        settings: The settings to change.
        email_sender: Sends the confirmation emails to added recipients.

    Returns:
        The website as it is now saved.

    Raises:
        NotFoundError: If the website does not exist.
        UndeliverableEmailError: If an email to an added recipient could not be delivered. Nothing is changed.
    """
    new_recipient_emails: list[str] = []
    if settings.add_recipient_emails:
        with db_context() as session:
            website_before: WebsiteRead = WebsiteService(session).get(website_id)
        new_recipient_emails = _not_yet_recipients(website_before, settings.add_recipient_emails)
        if new_recipient_emails:
            await confirm_addresses_can_receive_email(
                new_recipient_emails,
                subject=RECIPIENT_ADDED_SUBJECT,
                html_body=recipient_added_html(str(website_before.url)),
                email_sender=email_sender,
            )

    with db_context() as session:
        website: WebsiteRead = WebsiteService(session).update(website_id, settings.as_website_update())
        RecipientService(session).record_emailed(new_recipient_emails, emailed_at=datetime.now(UTC))
    return website


def delete_website(website_id: uuid.UUID) -> None:
    """Deletes a website, cancelling its scan if it is queued or being scanned.

    The deletion is saved before the scan is cancelled, so a cancelled first scan (which deletes the website it was
    adding) finds the website already gone instead of trying to delete it as well.

    Args:
        website_id: The ID of the website to delete.

    Raises:
        NotFoundError: If the website does not exist.
    """
    with db_context() as session:
        website_service = WebsiteService(session)
        website_url: HttpUrl = website_service.get(website_id).url
        website_service.delete(website_id)

    scan_queue.cancel(str(website_url))
