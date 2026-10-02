from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.backend.engine import get_critical_page_updates
from app.core.config import config
from app.core.errors import NotFoundError
from app.core.paths import resource_path
from app.db.core import db_context
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.frontend.api.utils import ContentBlockRecord, DailyRecord, TextChangeRecord, build_word_diff, website_name
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.models.recipient_models import RecipientCreate, RecipientUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanner import scan_all_websites, scan_website, send_monitoring_started_notifications, send_notification

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(directory=resource_path("app", "frontend", "templates"))
templates.env.filters["website_name"] = website_name


# ======================
# Views
# ======================


@ROOT_ROUTER.get("/")
def get_dashboard(session: SessionDep, request: Request):
    websites: Sequence[WebsiteRead] = WebsiteService(session).get_all()

    daily_records: list[DailyRecord] = []

    for website in websites:
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
                daily_records.append(
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
                    )
                )

    last_scans: list[datetime] = [website.last_scan_at for website in websites if website.last_scan_at]
    # %H rather than %-I, which is not supported on Windows where the desktop app runs
    daily_date: str | None = max(last_scans).strftime("%d %b %Y, %H:%M") if last_scans else None

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "daily_records": daily_records,
            "daily_date": daily_date,
            "websites": websites,
            "default_delay": config.web_crawler_default_delay,
            "default_concurrent": config.web_crawler_default_concurrent,
            "default_days_between_scans": config.scheduler_default_days_between_scans,
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


# TODO: Daily changes are currently unstyled.
# TODO: May be good to change "Add Website" to "Initialise Website"


@SCANNER_ROUTER.post("/initial_scan", response_model=None)
async def website_initial_scan(session: SessionDep, model_create: WebsiteCreate) -> None:
    """Registers a new website in the database, triggers an immediate initial crawl and
    emails the website's recipients to confirm it is now being monitored.

    Args:
        session (SessionDep): Database session dependency.
        model_create (WebsiteCreate): Payload containing details to create the website record.
    """
    website: WebsiteRead = WebsiteService(session).create(model_create)
    session.commit()

    async with AsyncClient() as client:
        await scan_website(client, website)

    with db_context() as session:
        WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=datetime.now()))

    send_monitoring_started_notifications(website)


@SCANNER_ROUTER.post("/initial_critical_page_scan", response_model=None)
async def critical_page_initial_scan(session: SessionDep, model_create: CriticalPageCreate) -> None:
    """Registers a new critical_page in the database and triggers an immediate initial crawl.

    Args:
        session (SessionDep): Database session dependency.
        model_create (CriticalPageCreate): Payload containing details to create the critical page record.
    """
    critical_page: CriticalPageRead = CriticalPageService(session).create(model_create)
    session.commit()

    async with AsyncClient() as client:
        updates: CriticalPageUpdate | None = await get_critical_page_updates(client, critical_page)

    if updates:
        with db_context() as session:
            CriticalPageService(session).update(id=critical_page.id, model_update=updates)


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_websites() -> str | None:
    """Triggers an asynchronous scan across all active, non-cooldown websites
    registered to the currently logged-in recipient.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.
    """
    return await scan_all_websites()


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
                send_notification(recipient.email, report)
            if recipient_email and recipient_email not in [r.email for r in website.recipients]:
                send_notification(recipient_email, report)

        return report


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
)
