from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import config
from app.core.errors import IntegrityError, NotFoundError, WebConnectionError
from app.core.logging import setup_logging
from app.core.paths import resource_path
from app.db.core import engine
from app.db.schema import Base
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
    StaticFiles(directory=resource_path("app", "frontend", "static")),
    name="static",
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


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.SCANNER_ROUTER)
app.include_router(routers.RECIPIENT_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)
