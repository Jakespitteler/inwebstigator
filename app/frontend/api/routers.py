import asyncio
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Form, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient, HTTPError
from sqlalchemy.orm import Session

from app.backend.email_service import confirm_address_can_receive_email, send_confirmation
from app.backend.engine import get_critical_page_updates
from app.backend.format_message import recipient_added_html
from app.backend.utils.http_client import fetch_content_from_url
from app.backend.utils.links import resolve_critical_page_url
from app.core.config import config
from app.core.errors import NotFoundError, ScanCancelledError, UndeliverableEmailError, WebCrawlerError
from app.core.paths import resource_path
from app.db.core import db_context
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.utils.interfaces import CRUDOperation
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.frontend.api.utils import (
    ContentBlockRecord,
    DailyRecord,
    TextChangeRecord,
    WebsiteDailyRecord,
    build_word_diff,
    format_timestamp,
    newest_first,
    scan_time,
    website_name,
)
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.models.recipient_models import RecipientCreate, RecipientUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanner import (
    cancel_scan,
    queued_crawls,
    scan_all_websites,
    scan_website,
    send_monitoring_started_notifications,
    send_notification,
)
from app.scheduler import next_scheduled_check, restart_scan_countdown

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(directory=resource_path("app", "frontend", "templates"))
templates.env.filters["website_name"] = website_name  # pyright: ignore[reportUnknownMemberType]
templates.env.filters["format_timestamp"] = format_timestamp  # pyright: ignore[reportUnknownMemberType]
templates.env.filters["scan_time"] = scan_time  # pyright: ignore[reportUnknownMemberType]


# ======================
# Views
# ======================


@ROOT_ROUTER.get("/")
def get_dashboard(session: SessionDep, request: Request):
    websites: Sequence[WebsiteRead] = WebsiteService(session).get_all()

    website_records: list[WebsiteDailyRecord] = []

    for website in websites:
        page_records: list[DailyRecord] = []

        for critical_page in website.critical_pages:
            changed: list[TextChangeRecord] = []
            added: list[ContentBlockRecord] = []
            removed: list[ContentBlockRecord] = []

            for change in critical_page.recent_text_changed or []:
                old_html, new_html = build_word_diff(
                    change.old_block.text,
                    change.new_block.text,
                )

                changed.append(
                    TextChangeRecord(
                        old_section=change.old_block.parent_heading,
                        new_section=change.new_block.parent_heading,
                        old=change.old_block.text,
                        new=change.new_block.text,
                        old_html=old_html,
                        new_html=new_html,
                        similarity=change.similarity,
                    )
                )

            for block in critical_page.recent_text_added or []:
                added.append(
                    ContentBlockRecord(
                        section=block.parent_heading,
                        text=block.text,
                        block_type=block.block_type.value,
                    )
                )

            for block in critical_page.recent_text_removed or []:
                removed.append(
                    ContentBlockRecord(
                        section=block.parent_heading,
                        text=block.text,
                        block_type=block.block_type.value,
                    )
                )

            links_added = list(critical_page.recent_links_added or [])
            links_removed = list(critical_page.recent_links_removed or [])
            documents_added = list(critical_page.recent_documents_added or [])
            documents_removed = list(critical_page.recent_documents_removed or [])

            has_changes = any(
                [
                    changed,
                    added,
                    removed,
                    links_added,
                    links_removed,
                    documents_added,
                    documents_removed,
                ]
            )

            if has_changes:
                page_records.append(
                    DailyRecord(
                        url=critical_page.url,
                        website_url=website.url,
                        changed=changed,
                        added=added,
                        removed=removed,
                        links_added=links_added,
                        links_removed=links_removed,
                        documents_added=documents_added,
                        documents_removed=documents_removed,
                        changed_at=critical_page.last_changed_at,
                    )
                )

        internal_links_added = list(website.recent_added_internal_links or [])
        internal_links_removed = list(website.recent_removed_internal_links or [])

        # Only websites with a changed critical page or internal link get a card on the updates page
        if page_records or internal_links_added or internal_links_removed:
            website_records.append(
                WebsiteDailyRecord(
                    website_url=website.url,
                    pages=newest_first(page_records),
                    internal_links_added=internal_links_added,
                    internal_links_removed=internal_links_removed,
                    internal_links_changed_at=website.internal_links_last_changed_at,
                )
            )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "website_records": newest_first(website_records),
            "next_check": next_scheduled_check(),
            "websites": websites,
            "default_delay": config.web_crawler_default_delay,
            "max_delay": config.web_crawler_max_delay,
            "default_concurrent": config.web_crawler_default_concurrent,
            "min_concurrent": config.web_crawler_min_concurrent,
            "default_days_between_scans": config.scheduler_default_days_between_scans,
            "minimum_days_between_scans": config.scheduler_minimum_days_between_scans,
            "max_pages": config.web_crawler_default_max_pages,
            "queued_website_urls": set(queued_crawls),
        },
    )


@ROOT_ROUTER.get("/about")
def get_about(request: Request):
    """Renders the About page: what the app does, the team, contact and copyright information."""
    return templates.TemplateResponse(request=request, name="about.html")


@ROOT_ROUTER.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    icon_path = resource_path("app", "frontend", "static", "favicon.ico")
    return FileResponse(icon_path)


# ======================
# Scanner
# ======================

SCANNER_ROUTER = APIRouter(prefix="/scanner", tags=["Scanner"])


# TODO: May be good to change "Add Website" to "Initialise Website"


def _discard_website(website_id: uuid.UUID) -> None:
    """Deletes a website whose first scan was cancelled, so cancelling the scan cancels adding the website.

    A website that has already been deleted is left alone, as deleting a website also cancels its scan.

    Args:
        website_id (uuid.UUID): The ID of the website to delete.
    """
    with db_context() as session, suppress(NotFoundError):
        WebsiteService(session).delete(website_id)


async def _check_pages_exist(urls: Sequence[str]) -> None:
    """Loads each page before it is added, so websites and critical pages that do not exist are not added.

    Args:
        urls (Sequence[str]): The full URLs of the pages to check.

    Raises:
        HTTPException: 422 if a page could not be loaded.
    """
    async with AsyncClient() as client:
        for url in urls:
            try:
                await fetch_content_from_url(client, url)
            except (WebCrawlerError, HTTPError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"{url} could not be loaded. Check it exists and the URL is correct.",
                ) from error


def _is_known_recipient(session: Session, email: str) -> bool:
    """Checks whether an email address is already a recipient, so it has already been confirmed.

    Args:
        session (Session): Database session.
        email (str): The email address to look for.
    """
    try:
        RecipientService(session).get_by_email(email)
    except NotFoundError:
        return False
    return True


async def _check_emails_can_be_received(session: Session, emails: Sequence[str], website_url: str) -> None:
    """Emails each address to confirm it, so an address that bounces is not added.

    An address that is already a recipient has been confirmed before, so it is emailed without waiting for a bounce.

    Args:
        session (Session): Database session.
        emails (Sequence[str]): The email addresses being added.
        website_url (str): The URL of the website the addresses are being added for.

    Raises:
        HTTPException: 422 if an email to an address could not be delivered.
    """
    subject: str = "Email address added to website monitoring"
    html_body: str = recipient_added_html(website_url)
    try:
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    send_confirmation if _is_known_recipient(session, email) else confirm_address_can_receive_email,
                    email,
                    subject,
                    html_body,
                )
                for email in dict.fromkeys(emails)
            )
        )
    except UndeliverableEmailError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"We can't send an email to {error.address}. Check the address is correct.",
        ) from error


@SCANNER_ROUTER.post("/initial_scan", response_model=None)
async def website_initial_scan(session: SessionDep, model_create: WebsiteCreate) -> None:
    """Registers a new website in the database, triggers an immediate initial crawl and
    emails the website's recipients to confirm it is now being monitored.

    A website or critical page that cannot be loaded is not added, and the request fails with a 422 status.
    A recipient that a confirmation email cannot be delivered to also fails the request with a 422 status.
    If the first scan is cancelled the website is not added, and the request fails with a 409 status.
    A website too large to scan is kept but deactivated, and only its critical pages are watched.

    Args:
        session (SessionDep): Database session dependency.
        model_create (WebsiteCreate): Payload containing details to create the website record.

    Raises:
        HTTPException: 422 if the website or one of its critical pages could not be loaded,
            or if an email to one of its recipients could not be delivered.
        ScanCancelledError: If the website's first scan was cancelled.
    """
    await _check_pages_exist([model_create.url, *model_create.critical_pages])
    await _check_emails_can_be_received(session, model_create.recipient_emails, model_create.url)
    website: WebsiteRead = WebsiteService(session).create(model_create)
    session.commit()

    try:
        async with AsyncClient() as client:
            await scan_website(client, website, init=True)
    except ScanCancelledError:
        _discard_website(website.id)
        raise

    with db_context() as session:
        website = WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=datetime.now()))

    send_monitoring_started_notifications(website)


def _discard_critical_page(critical_page_id: uuid.UUID) -> None:
    """Deletes a critical page whose first scan failed, so a page that cannot be scanned is not added.

    Args:
        critical_page_id (uuid.UUID): The ID of the critical page to delete.
    """
    with db_context() as session, suppress(NotFoundError):
        CriticalPageService(session).delete(critical_page_id)


@SCANNER_ROUTER.post("/initial_critical_page_scan", response_model=None)
async def critical_page_initial_scan(
    session: SessionDep,
    website_id: Annotated[uuid.UUID, Body()],
    url: Annotated[str, Body()],
) -> None:
    """Registers a new critical_page in the database and triggers an immediate initial crawl.

    If the first scan fails the critical page is not added, and the scan's error is raised.

    Args:
        session (SessionDep): Database session dependency.
        website_id (uuid.UUID): The website the critical page belongs to.
        url (str): The critical page as the user typed it. Can be a full URL, a URL without "https://"
            or a link relative to the website (e.g. "/news/today").

    Raises:
        HTTPException: 422 if the critical page is not a valid URL on the website or could not be loaded.
    """
    website: WebsiteRead = WebsiteService(session).get(website_id)
    try:
        model_create = CriticalPageCreate(website_id=website_id, url=resolve_critical_page_url(website.url, url))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    await _check_pages_exist([model_create.url])
    critical_page: CriticalPageRead = CriticalPageService(session).create(model_create)
    session.commit()

    try:
        async with AsyncClient() as client:
            updates: CriticalPageUpdate | None = await get_critical_page_updates(client, critical_page, init=True)
    except Exception:
        _discard_critical_page(critical_page.id)
        raise

    if updates:
        with db_context() as session:
            CriticalPageService(session).update(id=critical_page.id, model_update=updates)


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_websites() -> str | None:
    """Scans every website now for "Run All Scans", emailing each recipient one report of all the changes found.

    Websites that are not due a scan yet are included, while websites on cooldown are still skipped. The countdown
    to the next scheduled check restarts, as every website is being scanned now.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.
    """
    restart_scan_countdown()
    return await scan_all_websites(ignore_schedule=True)


@SCANNER_ROUTER.post("/run", response_model=str | None)
async def manually_scan_website(
    session: SessionDep,
    url: str = Form(...),
    recipient_email: str | None = Form(None),
    max_pages: int | None = Form(None),
    delay: float | None = Form(None),
    concurrent: int | None = Form(None),
) -> str | None:
    """Triggers the app from the UI form submission."""
    # Custom inputs are not currently being implemented by UI (we might want to keep it like this)
    try:
        website: WebsiteRead = WebsiteService(session).get_by_url(url)
    except NotFoundError:
        website: WebsiteRead = WebsiteService(session).create(model_create=WebsiteCreate(url=url))
    session.commit()

    async with AsyncClient() as client:
        report: str | None = await scan_website(client, website, max_pages, delay, concurrent)

    with db_context() as session:
        WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=datetime.now()))

    if report and website.recipients:
        if website.recipients:
            for recipient in website.recipients:
                send_notification(recipient.email, report, subject="Manual Website Scan")
            if recipient_email and recipient_email not in [r.email for r in website.recipients]:
                send_notification(recipient_email, report, subject="Manual Website Scan")

        return report


@SCANNER_ROUTER.post("/cancel", response_model=bool)
async def cancel_website_scan(url: str = Form(...)) -> bool:
    """Cancels a website's scan from the UI, whether it is waiting its turn or already running.

    Nothing found by the cancelled scan is saved, and the request that started it fails with a 409 status.

    Returns:
        bool: True if the scan was cancelled, or False if it had already finished.
    """
    return cancel_scan(url)


# ======================
# Database Operations
# ======================


RECIPIENT_ROUTER: APIRouter = create_crud_router(
    prefix="/recipients",
    service_class=RecipientService,
    create_class=RecipientCreate,
    update_class=RecipientUpdate,
)

CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service_class=CriticalPageService,
    create_class=CriticalPageCreate,
    update_class=CriticalPageUpdate,
)
WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service_class=WebsiteService,
    create_class=WebsiteCreate,
    update_class=WebsiteUpdate,
    exclude={CRUDOperation.UPDATE, CRUDOperation.DELETE},  # Replaced below, as each has extra behaviour
)


@WEBSITE_ROUTER.patch("/{id}", response_model=WebsiteRead)
async def update_website(session: SessionDep, id: uuid.UUID, model_update: WebsiteUpdate) -> WebsiteRead:
    """Updates a website, first confirming that any recipient being added can receive email.

    Args:
        session (SessionDep): Database session dependency.
        id (uuid.UUID): The ID of the website to update.
        model_update (WebsiteUpdate): The changes to make.

    Returns:
        WebsiteRead: The updated website.

    Raises:
        HTTPException: 422 if an email to an added recipient could not be delivered.
        NotFoundError: If no website has the ID.
    """
    website_service = WebsiteService(session)
    if model_update.add_recipient_emails:
        await _check_emails_can_be_received(session, model_update.add_recipient_emails, website_service.get(id).url)
    return website_service.update(id, model_update)


@WEBSITE_ROUTER.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_website(session: SessionDep, id: uuid.UUID) -> None:
    """Deletes a website, cancelling its scan if it is queued or being scanned.

    The deletion is committed before the scan is cancelled, so a cancelled first scan (which deletes
    the website it was adding) finds the website already gone instead of trying to delete it as well.

    Args:
        session (SessionDep): Database session dependency.
        id (uuid.UUID): The ID of the website to delete.

    Raises:
        NotFoundError: If no website has the ID.
    """
    website_service = WebsiteService(session)
    website: WebsiteRead = website_service.get(id)
    website_service.delete(id)
    session.commit()

    cancel_scan(website.url)
