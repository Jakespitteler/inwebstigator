import logging
from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.backend.web_scraper.engine import get_critical_page_state, get_website_state
from app.db.errors import NotFoundError
from app.db.models import critical_page_models, internal_link_models, website_models
from app.db.services import critical_page_service, internal_link_service, website_service
from app.frontend.api.crud_router_factory import create_crud_router
from app.frontend.api.dependencies import SessionDep
from app.scanner import scan_website

templates = Jinja2Templates(directory="app/frontend/templates")

logger = logging.getLogger(__name__)

ROOT_ROUTER = APIRouter()


@ROOT_ROUTER.get("/")
def get_root(session: SessionDep, request: Request) -> HTMLResponse:
    """
    Root endpoint to check if the server is running.

    Returns:
        A message indicating the server is running.
    """
    all_websites: Sequence[website_models.WebsiteRead] = website_service.WebsiteService(session).get_all()
    context: dict[str, str | list[str]] = {
        "title": "HomePage",
        "heading": "Digital Horizon Scan",
        "message": "Server is Running.",
        "websites": [website.url for website in all_websites],
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


SCANNER_ROUTER = APIRouter(prefix="/scanner")


@SCANNER_ROUTER.post("/run", response_class=HTMLResponse)
async def run_scanner_on_website(
    session: SessionDep,
    recipient_email: str = Form(...),
    url: str = Form(...),
    max_pages: int | None = Form(...),
    delay: float | None = Form(...),
    concurrent: int | None = Form(...),
) -> str:
    """Triggers the app from the UI form submission."""
    try:
        website: website_models.WebsiteRead = website_service.WebsiteService(session).get_by_url(url)
    except NotFoundError:
        website: website_models.WebsiteRead = website_service.WebsiteService(session).create(
            model_create=website_models.WebsiteCreate(url=url)
        )

    async with AsyncClient() as client:
        result_text: str = await scan_website(client, session, website, recipient_email, max_pages, delay, concurrent)

    return result_text


@SCANNER_ROUTER.post("/run_all", response_class=HTMLResponse)
async def run_scanner(session: SessionDep, recipient_email: str = Form(...)):
    """Triggers the app from the UI form submission."""
    all_websites: Sequence[website_models.WebsiteRead] = website_service.WebsiteService(session).get_all()

    result_text: list[str] = []
    async with AsyncClient() as client:
        for website in all_websites:
            if website.next_scan_at and website.next_scan_at > datetime.now():
                result_text.append(f"Skipped {website.url}: Cooldown active until {website.next_scan_at}.")
                continue
            result_text.append(await scan_website(client, session, website=website, recipient_email=recipient_email))

    html_content = f"<ul>{''.join(result_text)}</ul>"
    return HTMLResponse(content=html_content)


# ======================
# Database Operations
# ======================


CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service_class=critical_page_service.CriticalPageService,
    create_class=critical_page_models.CriticalPageCreate,
    update_class=critical_page_models.CriticalPageUpdate,
)


@CRITICAL_PAGE_ROUTER.post("/get_state", response_model=critical_page_models.CriticalPageState)
async def get_page_state(session: SessionDep, url: str = Form(...)) -> critical_page_models.CriticalPageState:
    stored_page: critical_page_models.CriticalPageRead = critical_page_service.CriticalPageService(session).get_by_url(
        url
    )
    async with AsyncClient() as client:
        return await get_critical_page_state(client, stored_page)


WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service_class=website_service.WebsiteService,
    create_class=website_models.WebsiteCreate,
    update_class=website_models.WebsiteUpdate,
)


@WEBSITE_ROUTER.post("/get_state", response_model=website_models.WebsiteState)
async def get_site_state(
    session: SessionDep,
    url: str = Form(...),
    max_pages: int | None = Form(...),
    delay: float | None = Form(...),
    concurrent: int | None = Form(...),
) -> website_models.WebsiteState:
    stored_website: website_models.WebsiteRead = website_service.WebsiteService(session).get_by_url(url=url)
    async with AsyncClient() as client:
        return await get_website_state(client, stored_website, max_pages, delay, concurrent)


INTERNAL_LINK_ROUTER: APIRouter = create_crud_router(
    prefix="/internal_links",
    service_class=internal_link_service.InternalLinkService,
    create_class=internal_link_models.InternalLinkCreate,
    update_class=internal_link_models.InternalLinkUpdate,
)


@INTERNAL_LINK_ROUTER.post(
    "/batch",
    response_model=Sequence[internal_link_models.InternalLinkRead],
    status_code=status.HTTP_201_CREATED,
)
def create_item_batch(
    session: SessionDep,
    batch_in: internal_link_models.InternalLinkCreateBatch,
) -> Sequence[internal_link_models.InternalLinkRead]:
    """
    Creates multiple internal links in batch for a single website.
    """
    return internal_link_service.InternalLinkService(session).create_batch(batch_in)
