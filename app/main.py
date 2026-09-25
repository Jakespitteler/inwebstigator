import sys
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import config
from app.core.errors import IntegrityError, InvalidCredentials, NotFoundError, NotLoggedInError, WebConnectionError
from app.core.logging import setup_logging
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


bundle_dir = getattr(sys, "_MEIPASS", None)
static_dir_base = Path("app/frontend/static")
static_dir = Path(bundle_dir) / static_dir_base if bundle_dir else static_dir_base

app.mount("/static", StaticFiles(directory=static_dir), name="static")


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


@app.exception_handler(NotLoggedInError)
async def not_logged_in_error_handler(request: Request, exc: NotLoggedInError):
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
async def invalid_credentials_error_handler(request: Request, exc: InvalidCredentials):
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


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.SCANNER_ROUTER)
app.include_router(routers.USER_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)
