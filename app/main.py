from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.backend.scanning.scheduler import schedule_scans
from app.core.config import config
from app.core.errors import (
    AlreadyWatchedError,
    IntegrityError,
    InvalidPageError,
    MainPageNotDeletableError,
    NotFoundError,
    PageNotLoadedError,
    ReportNotEmailedError,
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


# The status each of the app's errors is answered with. The error's own message is what the dashboard shows.
ERROR_STATUS_CODES: dict[type[Exception], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    IntegrityError: status.HTTP_400_BAD_REQUEST,
    # A website could not be reached, or a scan finished but its report could not be emailed (so the user is not
    # told the scan itself failed)
    WebConnectionError: status.HTTP_502_BAD_GATEWAY,
    ReportNotEmailedError: status.HTTP_502_BAD_GATEWAY,
    # A website or critical page that is already watched, or a scan that is already queued or was cancelled
    WebsiteAlreadyMonitoredError: status.HTTP_409_CONFLICT,
    AlreadyWatchedError: status.HTTP_409_CONFLICT,
    ScanAlreadyQueuedError: status.HTTP_409_CONFLICT,
    ScanCancelledError: status.HTTP_409_CONFLICT,
    # A website or critical page that cannot be added (not a valid page, or cannot be loaded), or a website's main
    # page, which cannot be deleted
    InvalidPageError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PageNotLoadedError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    MainPageNotDeletableError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def error_response(status_code: int) -> Callable[[Request, Exception], Coroutine[Any, Any, JSONResponse]]:
    """Builds an exception handler that answers with a status code and the error's message.

    Args:
        status_code: The HTTP status to answer with, e.g. 404.

    Returns:
        The exception handler.
    """

    async def respond_with_error(request: Request, exc: Exception) -> JSONResponse:
        """Answers a request that raised an error with the status code and the error's message."""
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return respond_with_error


for error_type, status_code in ERROR_STATUS_CODES.items():
    app.add_exception_handler(error_type, error_response(status_code))


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
