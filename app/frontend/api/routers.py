from collections.abc import Sequence

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.backend.web_scraper.site_crawler import crawl_site
from app.db.models import critical_page_models, internal_link_models, website_models
from app.db.services import critical_page_service, internal_link_service, website_service
from app.frontend.api.crud_router_factory import create_crud_router
from app.frontend.api.dependencies import SessionDep

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(directory="app/frontend/templates")


# ======================
# Views
# ======================


@ROOT_ROUTER.get("/", response_model=str)
def get_root(request: Request) -> HTMLResponse:
    """
    Root endpoint to check if the server is running.

    Returns:
        A message indicating the server is running.
    """
    context: dict[str, str] = {
        "title": "HomePage",
        "heading": "Digital Horizon Scan",
        "message": "Server is Running.",
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@ROOT_ROUTER.post("/crawl", response_class=HTMLResponse)
async def run_scraper(
    request: Request,
    url: str = Form(...),
    max_pages: int = Form(5000),
    max_concurrent: int = Form(5),
    delay: float = Form(1),
):
    """Triggers the scraper from the UI form submission."""
    async with AsyncClient() as client:
        discovered_links: set[str] = await crawl_site(
            client=client,
            url=url,
            max_pages=max_pages,
            max_concurrent=max_concurrent,
            delay=delay,
        )

    context: dict[str, str | list[str]] = {"links": list(discovered_links), "url": url}
    return templates.TemplateResponse(request=request, name="index.html", context=context)


# ======================
# Database Operations
# ======================


CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service_class=critical_page_service.CriticalPageService,
    create_class=critical_page_models.CriticalPageCreate,
    update_class=critical_page_models.CriticalPageUpdate,
)
WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service_class=website_service.WebsiteService,
    create_class=website_models.WebsiteCreate,
    update_class=website_models.WebsiteUpdate,
)
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
