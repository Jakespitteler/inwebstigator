from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.backend.scanning.scheduler import schedule_scans
from app.core.config import config
from app.core.errors import (
    AlreadyWatchedError,
    IntegrityError,
    InvalidPageError,
    NotFoundError,
    PageNotLoadedError,
    ScanAlreadyQueuedError,
    ScanCancelledError,
    UndeliverableEmailError,
    WebConnectionError,
    WebsiteAlreadyMonitoredError,
)
from app.core.logging_setup import setup_logging
from app.core.paths import resource_path
from app.db.migrations import prepare_database
from app.db.session import engine
from app.frontend.api import routers
from app.frontend.api.request_guard import only_accept_requests_from_the_dashboard

APP_NAME: str = config.app_name


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """FastAPI lifespan that gets the app ready before it takes requests, then runs the scheduled scans while the
    app is running, unless automatic scans are turned off.

    Logging and the database are set up here rather than when this module is imported, so importing the app (e.g.
    in a test) does not create log files or change the database.

    Args:
        app: The application instance.

    Yields:
        Control back to FastAPI while the app is running.
    """
    setup_logging(config.log_path, config.log_file_max_bytes, config.log_file_backup_count)
    prepare_database(engine)
    if not config.automatic_scans:
        yield
        return
    async with schedule_scans(app):
        yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.middleware("http")(only_accept_requests_from_the_dashboard)
app.mount(
    "/static",
    StaticFiles(directory=resource_path("app", "frontend", "static")),
    name="static",
)


@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError):
    """
    Handles NotFoundError exceptions by returning a 404 status.

    Args:
        request: The incoming request.
        exc: The NotFoundError exception.

    Returns:
        A JSONResponse with a 404 status.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(WebConnectionError)
async def web_connection_exception_handler(request: Request, exc: WebConnectionError):
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
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    Handles IntegrityError exceptions by returning a 400 status.

    Args:
        request: The incoming request.
        exc: The IntegrityError exception.

    Returns:
        A JSONResponse with a 400 status.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


@app.exception_handler(AlreadyWatchedError)
@app.exception_handler(ScanAlreadyQueuedError)
@app.exception_handler(ScanCancelledError)
async def scan_not_run_handler(
    request: Request, exc: AlreadyWatchedError | ScanAlreadyQueuedError | ScanCancelledError
):
    """
    Handles a critical page that is already watched, or a scan that is already queued or was cancelled, by
    returning a 409 status.

    Args:
        request: The incoming request.
        exc: The AlreadyWatchedError, ScanAlreadyQueuedError or ScanCancelledError exception.

    Returns:
        A JSONResponse with a 409 status.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


@app.exception_handler(InvalidPageError)
@app.exception_handler(PageNotLoadedError)
async def page_not_added_handler(request: Request, exc: InvalidPageError | PageNotLoadedError):
    """
    Handles a website or critical page that cannot be added, because it is not a valid page or cannot be loaded, by
    returning a 422 status.

    Args:
        request: The incoming request.
        exc: The InvalidPageError or PageNotLoadedError exception, whose message says what is wrong.

    Returns:
        A JSONResponse with a 422 status.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc)},
    )


@app.exception_handler(WebsiteAlreadyMonitoredError)
async def website_already_monitored_handler(request: Request, exc: WebsiteAlreadyMonitoredError):
    """
    Handles WebsiteAlreadyMonitoredError exceptions by returning a 409 status, with a message the dashboard shows.

    Args:
        request: The incoming request.
        exc: The WebsiteAlreadyMonitoredError exception.

    Returns:
        A JSONResponse with a 409 status.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


@app.exception_handler(UndeliverableEmailError)
async def undeliverable_email_handler(request: Request, exc: UndeliverableEmailError):
    """
    Handles an email address that cannot receive email, so is not added, by returning a 422 status.

    Args:
        request: The incoming request.
        exc: The UndeliverableEmailError exception.

    Returns:
        A JSONResponse with a 422 status.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": f"We can't send an email to {exc.address}. Check the address is correct."},
    )


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.SCANNER_ROUTER)
app.include_router(routers.RECIPIENT_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)
