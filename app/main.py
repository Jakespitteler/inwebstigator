from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import config
from app.core.logging import setup_logging
from app.db.core import Base, engine
from app.db.errors import IntegrityError, NotFoundError
from app.frontend.api import routers

setup_logging()

Base.metadata.create_all(bind=engine)
app = FastAPI(title=config.app_name)
app.mount("/static", StaticFiles(directory="app/frontend/static"), name="static")


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


# Register routes
app.include_router(routers.ROOT_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)
app.include_router(routers.INTERNAL_LINK_ROUTER)
