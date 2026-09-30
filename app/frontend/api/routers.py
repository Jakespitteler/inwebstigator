from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.core.paths import resource_path
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.frontend.api.utils import ContentBlockRecord, DailyRecord, TextChangeRecord, build_word_diff
from app.models.critical_page_models import CriticalPageCreate, CriticalPageUpdate
from app.models.recipient_models import RecipientCreate, RecipientUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanner import scan_all_websites, scan_website, send_monitoring_started_notifications

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(directory=resource_path("app", "frontend", "templates"))


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

    daily_date = None

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"daily_records": daily_records, "daily_date": daily_date, "websites": websites},
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

    send_monitoring_started_notifications(website)


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_websites() -> str | None:
    """Triggers an asynchronous scan across all active, non-cooldown websites
    registered to the currently logged-in recipient.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.
    """
    return await scan_all_websites()


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
