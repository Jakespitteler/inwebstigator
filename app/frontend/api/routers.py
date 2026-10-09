import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, Request, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import HttpUrl

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.scanning.all_websites_scan import scan_all_websites
from app.backend.scanning.manual_scan import scan_website_now
from app.backend.scanning.scan_queue import scan_queue
from app.backend.scanning.scheduler import next_scheduled_check, restart_scan_countdown
from app.backend.websites.website_setup import (
    add_critical_page,
    add_website,
    delete_website,
    update_website_settings,
)
from app.core.config import config
from app.core.paths import resource_path
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.crud_protocol import CRUDOperation
from app.db.services.recipient_service import RecipientService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.frontend.api.request_guard import API_TOKEN_HEADER
from app.frontend.api.utils import (
    WebsiteHistoryRecord,
    format_timestamp,
    newest_first,
    scan_time,
    website_history_record,
    website_name,
)
from app.models.critical_page_models import (
    CriticalPageCreate,
    CriticalPageRead,
    CriticalPageSettingsUpdate,
    CriticalPageUpdate,
)
from app.models.recipient_models import RecipientCreate, RecipientUpdate
from app.models.website_models import NewHttpUrl, WebsiteCreate, WebsiteRead, WebsiteSettingsUpdate, WebsiteUpdate

EmailSenderDep = Annotated[EmailSender, Depends(get_email_sender)]

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
    websites: Sequence[WebsiteRead] = WebsiteService(session).get_all(limit=None)

    website_names: dict[str, str] = {}
    for website in websites:
        saved_html: str | None = next(
            (page.text_body for page in website.critical_pages if page.url == website.url and page.text_body),
            None,
        )
        if saved_html or str(website.url) not in website_names:
            website_names[str(website.url)] = website_name(str(website.url), saved_html)

    scan_run_service = ScanRunService(session)
    website_records: list[WebsiteHistoryRecord] = [
        website_history_record(
            website.url, scan_run_service.get_latest_for_website(website.id, limit=config.scans_kept_per_website)
        )
        for website in websites
    ]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "website_records": newest_first(record for record in website_records if record.scans),
            "scans_kept_per_website": config.scans_kept_per_website,
            "next_check": next_scheduled_check(),
            "websites": websites,
            "website_names": website_names,
            "saved_recipient_emails": RecipientService(session).get_email_addresses(),
            "default_delay": config.web_crawler_default_delay,
            "max_delay": config.web_crawler_max_delay,
            "default_concurrent": config.web_crawler_default_concurrent,
            "min_concurrent": config.web_crawler_min_concurrent,
            "default_days_between_scans": config.scheduler_default_days_between_scans,
            "minimum_days_between_scans": config.scheduler_minimum_days_between_scans,
            "max_pages": config.web_crawler_default_max_pages,
            "queued_website_urls": set(scan_queue.queued_urls),
            "api_token": config.api_token,
            "api_token_header": API_TOKEN_HEADER,
        },
    )


@ROOT_ROUTER.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    icon_path = resource_path("app", "frontend", "static", "favicon.ico")
    return FileResponse(icon_path)


# ======================
# Scanner
# ======================

SCANNER_ROUTER = APIRouter(prefix="/scanner", tags=["Scanner"])


@SCANNER_ROUTER.post("/initial_scan", response_model=None)
async def website_initial_scan(email_sender: EmailSenderDep, model_create: WebsiteCreate) -> None:
    """Adds a website and scans it straight away to save its baseline (see `add_website`).

    A website already being watched fails with a 409 status, as does cancelling its first scan or adding a website
    that is already being added. A website or critical page that cannot be loaded, or a recipient that cannot be
    emailed, fails with a 422 status. In each case the website is not added.

    Args:
        email_sender (EmailSenderDep): Sends the emails to the recipients.
        model_create (WebsiteCreate): The website to add.
    """
    await add_website(model_create, email_sender)


@SCANNER_ROUTER.post("/initial_critical_page_scan", response_model=None)
async def critical_page_initial_scan(
    website_id: Annotated[uuid.UUID, Body()],
    url: Annotated[str, Body()],
) -> None:
    """Adds a critical page to a website and saves its baseline (see `add_critical_page`).

    A page that is not a valid URL on the website, or that cannot be loaded, fails with a 422 status. A page that is
    already being watched (even if written differently, e.g. with a trailing "/") fails with a 409 status.

    Args:
        website_id (uuid.UUID): The website the critical page belongs to.
        url (str): The critical page as the user typed it. Can be a full URL, a URL without "https://"
            or a link relative to the website (e.g. "/news/today").
    """
    await add_critical_page(website_id, url)


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_websites(email_sender: EmailSenderDep) -> str | None:
    """Scans every website now for "Run All Scans", emailing each recipient one report of all the changes found.

    Websites that are not due a scan yet are included, while websites on cooldown are still skipped. The countdown
    to the next scheduled check restarts, as every website is being scanned now.

    Args:
        email_sender (EmailSenderDep): Sends the reports.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.
    """
    restart_scan_countdown()
    return await scan_all_websites(ignore_schedule=True, email_sender=email_sender)


@SCANNER_ROUTER.post("/run", response_model=str | None)
async def manually_scan_website(
    email_sender: EmailSenderDep,
    url: Annotated[NewHttpUrl, Form()],
    recipient_email: Annotated[str | None, Form()] = None,
    max_pages: Annotated[int | None, Form()] = None,
    delay: Annotated[float | None, Form()] = None,
    concurrent: Annotated[int | None, Form()] = None,
) -> str | None:
    """Scans a monitored website straight away for "Run Scan Now", and emails its report (see `scan_website_now`).

    A website that is not monitored fails with a 404 status. A website already queued or being scanned, or whose
    scan is cancelled, fails with a 409 status.

    Args:
        email_sender (EmailSenderDep): Sends the report.
        url (NewHttpUrl): The URL of the website.
        recipient_email (str | None): Another address to send the report to.
        max_pages (int | None): The most pages to crawl, or None for the default.
        delay (float | None): Seconds to wait between requests, or None for the website's own.
        concurrent (int | None): The most requests at once, or None for the website's own.

    Returns:
        str | None: The report as HTML if changes were found or the scan ran into a problem, otherwise None.
    """
    return await scan_website_now(url, email_sender, recipient_email, max_pages, delay, concurrent)


@SCANNER_ROUTER.post("/cancel", response_model=bool)
async def cancel_website_scan(url: Annotated[HttpUrl, Form()]) -> bool:
    """Cancels a website's scan from the UI, whether it is waiting its turn or already running.

    Nothing found by the cancelled scan is saved, and the request that started it fails with a 409 status.

    Returns:
        bool: True if the scan was cancelled, or False if it had already finished.
    """
    return scan_queue.cancel(str(url))


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
    # Critical pages are only added through /scanner/initial_critical_page_scan, which checks the page is on the
    # website and loads, and saves its baseline. Updating is replaced below, so only the page's settings can change.
    exclude={CRUDOperation.CREATE, CRUDOperation.UPDATE},
)
WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service_class=WebsiteService,
    create_class=WebsiteCreate,
    update_class=WebsiteUpdate,
    # Replaced below: websites are only added through /scanner/initial_scan, which checks them and saves a baseline,
    # and updating or deleting one has extra behaviour
    exclude={CRUDOperation.CREATE, CRUDOperation.UPDATE, CRUDOperation.DELETE},
)


@CRITICAL_PAGE_ROUTER.patch("/{id}", response_model=CriticalPageRead)
async def update_critical_page(
    session: SessionDep, id: uuid.UUID, settings: CriticalPageSettingsUpdate
) -> CriticalPageRead:
    """Changes a critical page's settings, e.g. its ignore rules.

    Args:
        session (SessionDep): Database session dependency.
        id (uuid.UUID): The ID of the critical page to change.
        settings (CriticalPageSettingsUpdate): The settings to change.

    Returns:
        CriticalPageRead: The critical page as it is now saved.

    Raises:
        NotFoundError: If no critical page has the ID.
    """
    return CriticalPageService(session).update(id, settings.as_critical_page_update())


@WEBSITE_ROUTER.patch("/{id}", response_model=WebsiteRead)
async def update_website(id: uuid.UUID, email_sender: EmailSenderDep, settings: WebsiteSettingsUpdate) -> WebsiteRead:
    """Changes a website's settings, first confirming that any recipient being added can receive email.

    A recipient that cannot be emailed fails with a 422 status, and nothing is changed.

    Args:
        id (uuid.UUID): The ID of the website to change.
        email_sender (EmailSenderDep): Sends the confirmation emails to added recipients.
        settings (WebsiteSettingsUpdate): The settings to change.

    Returns:
        WebsiteRead: The website as it is now saved.

    Raises:
        NotFoundError: If no website has the ID.
    """
    return await update_website_settings(id, settings, email_sender)


@WEBSITE_ROUTER.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_website(id: uuid.UUID) -> None:
    """Deletes a website, cancelling its scan if it is queued or being scanned.

    Args:
        id (uuid.UUID): The ID of the website to delete.

    Raises:
        NotFoundError: If no website has the ID.
    """
    delete_website(id)
