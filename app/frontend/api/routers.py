import uuid
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.backend.format_message import ScanStatus, generate_scan_report_html
from app.core.config import config
from app.core.errors import InvalidCredentials, NotLoggedInError
from app.db.services import critical_page_service, user_service, website_service
from app.db.utils.field_types import EmailString
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
async def website_initial_scan(session: SessionDep, model_create: website_models.WebsiteCreate) -> str:
    website: website_models.WebsiteRead = website_service.WebsiteService(session).create(model_create)

    async with AsyncClient() as client:
        html_report = await scan_website(client, session, website)

    return html_report


@SCANNER_ROUTER.post("/run", response_model=str)
async def scan_a_website(
    session: SessionDep,
    url: str = Form(...),
    max_pages: int | None = Form(None),
    delay: float | None = Form(None),
    concurrent: int | None = Form(None),
) -> str:
    website: website_models.WebsiteRead = website_service.WebsiteService(session).get_by_url(url)

    async with AsyncClient() as client:
        html_report = await scan_website(client, session, website, max_pages, delay, concurrent)

    user: user_models.UserRead = user_service.UserService(session).get(id=website.user_id)
    send_notification(user.email, html_report)
    return html_report


@SCANNER_ROUTER.post("/run_all", response_model=str)
async def scan_all_websites(session: SessionDep) -> str:
    if not config.user_id:
        raise NotLoggedInError()

    user: user_models.UserRead = user_service.UserService(session).get(id=config.user_id)

    reports: list[str] = []
    async with AsyncClient() as client:
        for website in user.websites:
            if not website.active:
                reports.append(
                    generate_scan_report_html(
                        website,
                        status=ScanStatus.SKIPPED_DEACTIVATED,
                        message="Website has been deactivated due to consecutive scan failures.",
                    )
                )
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                reports.append(
                    generate_scan_report_html(
                        website,
                        status=ScanStatus.SKIPPED_COOLDOWN,
                        message=f"Cooldown active until {website.on_cooldown_until:%d %b %Y, %H:%M UTC}.",
                    )
                )
                continue

            reports.append(await scan_website(client, session, website))

    joint_reports = f"<ul>{''.join(reports)}</ul>"

    send_notification(user.email, joint_reports)
    return joint_reports


# ======================
# Database Operations
# ======================


USER_ROUTER: APIRouter = create_crud_router(
    prefix="/users",
    service_class=user_service.UserService,
    create_class=user_models.UserCreate,
    update_class=user_models.UserUpdate,
)


@USER_ROUTER.post("/log_in", response_model=str)
async def log_in(session: SessionDep, email: str = Form(...), password: str = Form(...)) -> str:
    user = user_service.UserService(session).get_by_email(email)

    if user.password != password:
        raise InvalidCredentials("Password was incorrect")

    config.user_id = user.id
    return "Successfully logged in"


@USER_ROUTER.post("/log_out", response_model=str)
async def log_out() -> str:
    config.user_id = None
    return "Successfully logged out"


@USER_ROUTER.post("/current_user", response_model=tuple[uuid.UUID, EmailString])
async def get_current_user(session: SessionDep) -> tuple[uuid.UUID, EmailString]:
    if not config.user_id:
        raise NotLoggedInError()

    current_user = user_service.UserService(session).get(id=config.user_id)

    return current_user.id, current_user.email


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
