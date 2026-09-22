from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient

from app.core.config import config
from app.core.errors import NotLoggedInError
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.models.critical_page_models import CriticalPageCreate, CriticalPageUpdate
from app.models.user_models import UserCreate, UserRead, UserUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanner import scan_user_websites, scan_website, send_notification

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


@SCANNER_ROUTER.post("/initial_scan", response_model=None)
async def website_initial_scan(session: SessionDep, model_create: WebsiteCreate) -> None:
    website: WebsiteRead = WebsiteService(session).create(model_create)

    async with AsyncClient() as client:
        await scan_website(client, session, website)


@SCANNER_ROUTER.post("/run", response_model=str | None)
async def manually_scan_a_website(
    session: SessionDep,
    url: str = Form(...),
    max_pages: int | None = Form(None),
    delay: float | None = Form(None),
    concurrent: int | None = Form(None),
) -> str | None:
    website: WebsiteRead = WebsiteService(session).get_by_url(url)

    async with AsyncClient() as client:
        html_report: str | None = await scan_website(client, session, website, max_pages, delay, concurrent)

    if html_report:
        user_service = UserService(session)
        user: UserRead = user_service.get(id=website.user_id)
        send_notification(session, user, html_report)
        return html_report  # TODO maybe return "no changes found"


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_websites_for_a_user(session: SessionDep) -> str | None:
    if not config.user_id:
        raise NotLoggedInError()

    user: UserRead = UserService(session).get(id=config.user_id)
    return await scan_user_websites(session, user)  # TODO maybe return "no changes found"


# ======================
# Database Operations
# ======================


USER_ROUTER: APIRouter = create_crud_router(
    prefix="/users",
    service_class=UserService,
    create_class=UserCreate,
    update_class=UserUpdate,
)


@USER_ROUTER.post("/log_in", response_model=str)
async def log_in(session: SessionDep, email: str = Form(...), password: str = Form(...)) -> str:
    return UserService(session).log_in(email, password)


@USER_ROUTER.post("/log_out", response_model=str)
async def log_out(session: SessionDep) -> str:
    return UserService(session).log_out()


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
