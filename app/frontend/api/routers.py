import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import HttpUrl

from app.backend.email_service.delivery import EmailSender, get_email_sender
from app.backend.scanning.all_websites_scan import cancel_run_all, run_all_in_progress, scan_all_websites_now
from app.backend.scanning.manual_scan import scan_website_now
from app.backend.scanning.scan_queue import scan_queue
from app.backend.scanning.scheduler import next_scheduled_check
from app.backend.websites.website_setup import (
    add_critical_page,
    add_website,
    delete_critical_page,
    delete_website,
    update_website_settings,
)
from app.backend.websites.website_titles import saved_home_page_html, website_card_title
from app.core.config import config
from app.core.paths import resource_path
from app.core.urls import website_name
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.crud_protocol import CRUDOperation
from app.db.services.recipient_service import RecipientService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.frontend.api.dashboard_records import (
    WebsiteHistoryRecord,
    format_timestamp,
    newest_first,
    scan_time,
    website_history_record,
)
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.frontend.api.request_guard import API_TOKEN_HEADER
from app.models.critical_page_models import (
    CriticalPageCreate,
    CriticalPageRead,
    CriticalPageSettingsUpdate,
    CriticalPageUpdate,
)
from app.models.recipient_models import RecipientCreate, RecipientUpdate
from app.models.scan_run_models import ScanRunRead
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
    # Each scan saves its website's card title. One not saved yet (e.g. before the website's first scan since the
    # app was updated) is read from the saved home page until then.
    website_names: dict[str, str] = {
        str(website.url): website.card_title or website_card_title(str(website.url), saved_home_page_html(website))
        for website in websites
    }

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
            "run_all_in_progress": run_all_in_progress(),
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


@SCANNER_ROUTER.post("/initial_scan", response_model=ScanRunRead | None)
async def website_initial_scan(email_sender: EmailSenderDep, model_create: WebsiteCreate) -> ScanRunRead | None:
    """Adds a website and scans it straight away to save its baseline (see `add_website`).

    A website already being watched fails with a 409 status, as does cancelling its first scan or adding a website
    that is already being added. A website or critical page that cannot be loaded, or a recipient that cannot be
    emailed, fails with a 422 status. In each case the website is not added.

    A website whose first scan ran into a problem (e.g. it blocked the crawler) is still added, so the problem is
    returned in the scan for the dashboard to show.

    Args:
        email_sender (EmailSenderDep): Sends the emails to the recipients.
        model_create (WebsiteCreate): The website to add.

    Returns:
        ScanRunRead | None: The website's first scan, or None if it has none.
    """
    website: WebsiteRead = await add_website(model_create, email_sender)
    with db_context() as session:
        first_scans: list[ScanRunRead] = ScanRunService(session).get_latest_for_website(website.id, limit=1)
    return first_scans[0] if first_scans else None


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

    Websites that are not due a scan yet are included, while websites on cooldown are still skipped. The scheduled
    checks carry on at their usual times, and only scan these websites again once they are next due. It can be
    stopped part way through with `/scanner/cancel_all`.

    Args:
        email_sender (EmailSenderDep): Sends the reports.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.

    Raises:
        HTTPException: 409 if "Run All Scans" is already running.
    """
    if run_all_in_progress():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run All Scans is already running.")

    return await scan_all_websites_now(email_sender=email_sender)


@SCANNER_ROUTER.post("/cancel_all", response_model=bool)
async def cancel_all_scans() -> bool:
    """Cancels "Run All Scans": the website being scanned is cancelled and the rest are skipped.

    Websites already scanned keep their results, and their changes are still emailed.

    Returns:
        bool: True if it was cancelled, or False if "Run All Scans" was not running.
    """
    return cancel_run_all()


@SCANNER_ROUTER.post("/run", response_model=str | None)
async def manually_scan_website(
    email_sender: EmailSenderDep,
    url: Annotated[NewHttpUrl, Form()],
    recipient_email: Annotated[str | None, Form()] = None,
    max_pages: Annotated[int | None, Form()] = None,
    delay: Annotated[float | None, Form()] = None,
    concurrent: Annotated[int | None, Form(ge=config.web_crawler_min_concurrent)] = None,
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
    # Recipients are only added and removed through their websites (PATCH /websites/{id}), which emails each added
    # address to confirm it, so they can only be read here
    exclude={CRUDOperation.CREATE, CRUDOperation.UPDATE, CRUDOperation.DELETE},
)

CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service_class=CriticalPageService,
    create_class=CriticalPageCreate,
    update_class=CriticalPageUpdate,
    # Critical pages are only added through /scanner/initial_critical_page_scan, which checks the page is on the
    # website and loads, and saves its baseline. Updating and deleting are replaced below, so only the page's settings
    # can change and a website's main page cannot be deleted.
    exclude={CRUDOperation.CREATE, CRUDOperation.UPDATE, CRUDOperation.DELETE},
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


@CRITICAL_PAGE_ROUTER.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_critical_page(id: uuid.UUID) -> None:
    """Stops watching a critical page. A website's main page fails with a 422 status, as it is always watched.

    Args:
        id (uuid.UUID): The ID of the critical page to delete.

    Raises:
        NotFoundError: If no critical page has the ID.
    """
    delete_critical_page(id)


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
