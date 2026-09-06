import json
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.core.config import config
from app.core.logging import setup_logging
from app.db.core import Base, engine
from app.db.errors import IntegrityError, NotFoundError
from app.frontend.api import routers


setup_logging()

Base.metadata.create_all(bind=engine)

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


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request,
    exc: IntegrityError,
):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


@app.get("/dashboard")
def get_dashboard(request: Request):
    data_file = Path("data/sample_change_history.json")

    with data_file.open("r", encoding="utf-8") as file:
        records = json.load(file)

    today = datetime.now().astimezone()
    week_start = today - timedelta(days=6)

    weekly_records = []

    for record in records:
        detected_at = datetime.fromisoformat(record["detected_at"])

        if week_start.date() <= detected_at.date() <= today.date():
            for change in record.get("changed", []):
                old_html, new_html = build_word_diff(
                    change["old"],
                    change["new"],
                )

                change["old_html"] = old_html
                change["new_html"] = new_html

            weekly_records.append(record)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "records": weekly_records,
            "week_start": week_start.strftime("%d %b %Y"),
            "week_end": today.strftime("%d %b %Y"),
        },
    )


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)
app.include_router(routers.INTERNAL_LINK_ROUTER)