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
    """Renders and returns the application homepage HTML template.

    Args:
        request (Request): The incoming FastAPI HTTP request instance.

    Returns:
        HTMLResponse: Rendered `index.html` template populated with header context.
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
    """Registers a new website in the database and triggers an immediate initial crawl.

    Args:
        session (SessionDep): Database session dependency.
        model_create (WebsiteCreate): Payload containing details to create the website record.
    """
    website: WebsiteRead = WebsiteService(session).create(model_create)

    async with AsyncClient() as client:
        session.commit()
        await scan_website(client, website)


@SCANNER_ROUTER.post("/run", response_model=str | None)
async def manually_scan_a_website(
    session: SessionDep,
    url: str = Form(...),
    max_pages: int | None = Form(None),
    delay: float | None = Form(None),
    concurrent: int | None = Form(None),
) -> str | None:
    """Manually triggers a scan for a specific website by its URL with optional override limits.

    If updates or changes are detected during the scan, dispatches an email notification
    to the website owner.

    Args:
        session (SessionDep): Database session dependency.
        url (str): Target website URL submitted via form data.
        max_pages (int | None, optional): Optional override for maximum pages to crawl.
        delay (float | None, optional): Optional override for inter-request delay in seconds.
        concurrent (int | None, optional): Optional override for max concurrent connections.

    Returns:
        str | None: HTML formatted scan report if changes/errors occurred, otherwise None.
    """
    website: WebsiteRead = WebsiteService(session).get_by_url(url)
    user_service = UserService(session)
    user: UserRead = user_service.get(id=website.user_id)

    async with AsyncClient() as client:
        session.commit()
        html_report: str | None = await scan_website(client, website, max_pages, delay, concurrent)

    if html_report:
        send_notification(user, html_report)
        return html_report  # TODO maybe return "no changes found" if no html_report


@SCANNER_ROUTER.post("/run_all", response_model=str | None)
async def scan_logged_in_user_websites(session: SessionDep) -> str | None:
    """Triggers an asynchronous scan across all active, non-cooldown websites
    registered to the currently logged-in user.

    Args:
        session (SessionDep): Database session dependency.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates occurred, otherwise None.

    Raises:
        NotLoggedInError: If no authenticated user ID is configured in application settings.
    """
    if not config.user_id:
        raise NotLoggedInError()

    user: UserRead = UserService(session).get(id=config.user_id)
    session.commit()
    return await scan_user_websites(user)  # TODO maybe return "no changes found"


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
    """Authenticates a user using email and password form credentials.

    Args:
        session (SessionDep): Database session dependency.
        email (str): Registered user email address.
        password (str): User account password.

    Returns:
        str: Authentication status or session token message.
    """
    return UserService(session).log_in(email, password)


@USER_ROUTER.post("/log_out", response_model=str)
async def log_out(session: SessionDep) -> str:
    """Logs out the active user and clears current session state.

    Args:
        session (SessionDep): Database session dependency.

    Returns:
        str: Confirmation message confirming log out.
    """
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
