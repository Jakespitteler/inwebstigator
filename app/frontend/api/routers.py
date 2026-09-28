from difflib import SequenceMatcher
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from httpx2 import AsyncClient
from markupsafe import Markup, escape

from app.core.config import config
from app.core.errors import NotLoggedInError
from app.core.paths import resource_path
from app.db.core import SessionLocal
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.frontend.api.db_router_factory import SessionDep, create_crud_router
from app.models.critical_page_models import CriticalPageCreate, CriticalPageUpdate
from app.models.user_models import UserCreate, UserRead, UserUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanner import scan_user_websites, scan_website, send_notification

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(
    directory=resource_path("app", "frontend", "templates")
)

def build_word_diff(old_text: str, new_text: str) -> tuple[Markup, Markup]:
    old_words = old_text.split()
    new_words = new_text.split()

    matcher = SequenceMatcher(
        None,
        old_words,
        new_words,
        autojunk=False,
    )

    old_parts = []
    new_parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            old_parts.extend(escape(word) for word in old_words[i1:i2])
            new_parts.extend(escape(word) for word in new_words[j1:j2])

        elif tag == "delete":
            old_parts.extend(
                Markup(f'<span class="removed-word">{escape(word)}</span>')
                for word in old_words[i1:i2]
            )

        elif tag == "insert":
            new_parts.extend(
                Markup(f'<span class="added-word">{escape(word)}</span>')
                for word in new_words[j1:j2]
            )

        elif tag == "replace":
            old_parts.extend(
                Markup(f'<span class="removed-word">{escape(word)}</span>')
                for word in old_words[i1:i2]
            )

            new_parts.extend(
                Markup(f'<span class="added-word">{escape(word)}</span>')
                for word in new_words[j1:j2]
            )

    return (
        Markup(" ").join(old_parts),
        Markup(" ").join(new_parts),
    )

# ======================
# Views
# ======================


@ROOT_ROUTER.get("/dashboard")
def get_dashboard(request: Request):
    if config.user_id is None:
        return RedirectResponse(
            url="/login",
            status_code=303,
        )

    with SessionLocal() as session:
        website_service = WebsiteService(session)
        website_summaries = website_service.get_all()

        websites = [
            website_service.get(website.id)
            for website in website_summaries
        ]

        user_service = UserService(session)
        users = user_service.get_all()

        current_user = user_service.get(id=config.user_id)

        daily_records = []

        for website in websites:
            for critical_page in website.critical_pages:
                changed = []
                added = []
                removed = []

                for change in critical_page.recent_text_changed or []:
                    old_html, new_html = build_word_diff(
                        change.old_block.text,
                        change.new_block.text,
                    )

                    changed.append(
                        {
                            "old_section": change.old_block.parent_heading,
                            "new_section": change.new_block.parent_heading,
                            "old": change.old_block.text,
                            "new": change.new_block.text,
                            "old_html": old_html,
                            "new_html": new_html,
                            "similarity": change.similarity,
                        }
                    )

                for block in critical_page.recent_text_added or []:
                    added.append(
                        {
                            "section": block.parent_heading,
                            "text": block.text,
                            "block_type": block.block_type.value,
                        }
                    )

                for block in critical_page.recent_text_removed or []:
                    removed.append(
                        {
                            "section": block.parent_heading,
                            "text": block.text,
                            "block_type": block.block_type.value,
                        }
                    )

                links_added = list(
                    critical_page.recent_links_added or []
                )
                links_removed = list(
                    critical_page.recent_links_removed or []
                )
                documents_added = list(
                    critical_page.recent_documents_added or []
                )
                documents_removed = list(
                    critical_page.recent_documents_removed or []
                )

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
                        {
                            "url": critical_page.url,
                            "website_url": website.url,
                            "changed": changed,
                            "added": added,
                            "removed": removed,
                            "links_added": links_added,
                            "links_removed": links_removed,
                            "documents_added": documents_added,
                            "documents_removed": documents_removed,
                        }
                    )

        daily_date = None

        if current_user and current_user.last_scan_at:
            daily_date = current_user.last_scan_at.strftime("%d %b %Y")

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "daily_records": daily_records,
            "daily_date": daily_date,
            "websites": websites,
            "users": users,
            "current_user": current_user,
        },
    )

@ROOT_ROUTER.get("/login")
def get_login(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
    )


@ROOT_ROUTER.get("/signup")
def get_signup(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
    )

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
    session.commit()

    async with AsyncClient() as client:
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
    session.commit()

    async with AsyncClient() as client:
        html_report: str | None = await scan_website(client, website, max_pages, delay, concurrent)

    if html_report:
        send_notification(user, html_report)
        return html_report  # TODO maybe return "no changes found" if no changes are found


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
    return await scan_user_websites(user)  # TODO maybe return "no changes found" if no changes are found


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
