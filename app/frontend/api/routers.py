import uuid
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.db.services import critical_page_service, user_service, website_service
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.models import critical_page_models, user_models, website_models
from app.scanner import scan_website, send_notification

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
        "heading": "Inwebstigator",
        "message": "Server is Running.",
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


# ======================
# Crawler
# ======================

SCANNER_ROUTER = APIRouter(prefix="/scanner", tags=["Scanner"])


@SCANNER_ROUTER.post("/initial_scan", response_model=str)
async def website_initial_scan(session: SessionDep, model_create: website_models.WebsiteCreate) -> HTMLResponse:
    website: website_models.WebsiteRead = website_service.WebsiteService(session).create(model_create)

    async with AsyncClient() as client:
        result_text: str = await scan_website(client, session, website)
    return HTMLResponse(content=result_text)  # TODO: have different result text


@SCANNER_ROUTER.post("/run", response_model=str)
async def scan_a_website(
    session: SessionDep,
    url: str = Form(...),
    user_id: str = Form(...),
    max_pages: int | None = Form(None),
    delay: float | None = Form(None),
    concurrent: int | None = Form(None),
) -> HTMLResponse:
    user = user_service.UserService(session).get(id=uuid.UUID(user_id))
    website: website_models.WebsiteRead = website_service.WebsiteService(session).get_by_url(url, user.id)

    async with AsyncClient() as client:
        report: str = await scan_website(client, session, website, max_pages, delay, concurrent)

    send_notification(user.email, report)
    return HTMLResponse(content=report)


@SCANNER_ROUTER.post("/run_all", response_model=str)
async def scan_all_websites(session: SessionDep, user_id: str = Form(...)) -> HTMLResponse:
    user = user_service.UserService(session).get(id=uuid.UUID(user_id))

    reports: list[str] = []
    async with AsyncClient() as client:
        for website in user.websites:
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                reports.append(f"Skipped {website.url}: Cooldown active until {website.on_cooldown_until}.")
                continue
            reports.append(await scan_website(client, session, website))
    joint_reports = f"<ul>{''.join(reports)}</ul>"

    send_notification(user.email, joint_reports)
    return HTMLResponse(content=joint_reports)


# ======================
# Database Operations
# ======================


USER_ROUTER: APIRouter = create_crud_router(
    prefix="/users",
    service_class=user_service.UserService,
    create_class=user_models.UserCreate,
    update_class=user_models.UserUpdate,
)
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
