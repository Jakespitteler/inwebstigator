from difflib import SequenceMatcher

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.core.config import config
from app.core.errors import (
    IntegrityError,
    InvalidCredentials,
    NotFoundError,
    NotLoggedInError,
    WebConnectionError,
)
from app.core.logging import setup_logging
from app.db.core import SessionLocal, engine
from app.db.schema import Base
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.frontend.api import routers
from app.scheduler import schedule_scans

setup_logging()

Base.metadata.create_all(bind=engine)

if config.automatic_scans:
    app = FastAPI(title=config.app_name, lifespan=schedule_scans)
else:
    app = FastAPI(title=config.app_name)

app.mount(
    "/static",
    StaticFiles(directory="app/frontend/static"),
    name="static",
)

templates = Jinja2Templates(directory="app/templates")


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


@app.exception_handler(NotFoundError)
async def not_found_exception_handler(
    request: Request,
    exc: NotFoundError,
):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(WebConnectionError)
async def web_connection_exception_handler(
    request: Request,
    exc: WebConnectionError,
):
    """
    Handles WebConnectionError exceptions by returning a 502 status.

    Args:
        request: The incoming request.
        exc: The WebConnectionError exception.

    Returns:
        A JSONResponse with a 502 status.
    """
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc)},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request,
    exc: IntegrityError,
):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


@app.exception_handler(NotLoggedInError)
async def not_logged_in_error_handler(
    request: Request,
    exc: NotLoggedInError,
):
    """
    Handles NotLoggedInError exceptions by returning a 400 status.

    Args:
        request: The incoming request.
        exc: The NotLoggedInError exception.

    Returns:
        A JSONResponse with a 400 status.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


@app.exception_handler(InvalidCredentials)
async def invalid_credentials_error_handler(
    request: Request,
    exc: InvalidCredentials,
):
    """
    Handles InvalidCredentials exceptions by returning a 401 status.

    Args:
        request: The incoming request.
        exc: The InvalidCredentials exception.

    Returns:
        A JSONResponse with a 401 status.
    """
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )

@app.get("/login")
def get_login(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
    )

@app.get("/signup")
def get_signup(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
    )

@app.get("/dashboard")
def get_dashboard(request: Request):
    with SessionLocal() as session:
        website_service = WebsiteService(session)
        website_summaries = website_service.get_all()

        websites = [
            website_service.get(website.id)
            for website in website_summaries
        ]

        user_service = UserService(session)
        users = user_service.get_all()

        current_user = None
        if websites:
            current_user = user_service.get(id=websites[0].user_id)
        elif len(users) == 1:
            current_user = users[0]

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
        },
    )


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.SCANNER_ROUTER)
app.include_router(routers.USER_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)