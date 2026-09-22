import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI

from app.core.config import config
from app.db.core import get_db_session
from app.db.services.user_service import UserService
from app.models.user_models import UserRead
from app.scanner import scan_user_websites, send_notification

logger = logging.getLogger(__name__)

DAYS_BETWEEN_SCANS: int = config.days_between_scans
DAYS_BETWEEN_HEALTH_CHECKS: int = config.days_between_heath_checks
ADMIN_ID: uuid.UUID = uuid.uuid4()

scheduler = AsyncIOScheduler()
db_context = contextmanager(get_db_session)


async def scan_with_fresh_db_session(user: UserRead) -> None:
    """Wrapper to instantiate a fresh database session for each scheduled run."""
    with db_context() as session:
        await scan_user_websites(session, user)


def send_heath_check_if_no_change(user: UserRead) -> None:
    if not user.last_email_at or (datetime.now() - user.last_email_at) > timedelta(days=DAYS_BETWEEN_HEALTH_CHECKS):
        with db_context() as session:
            send_notification(session, user, report="No changes have been found since the last notification")


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    if not config.user_id:
        config.user_id = ADMIN_ID

    with db_context() as session:
        users = UserService(session).get_all()
        for user in users:
            if not user.last_scan_at or (datetime.now() - user.last_scan_at) > timedelta(days=DAYS_BETWEEN_SCANS):
                logger.info("Missed scan interval detected. Running scan immediately on startup...")
                await scan_user_websites(session, user)
            send_heath_check_if_no_change(user)

    for user in users:
        scheduler.add_job(scan_with_fresh_db_session, "interval", days=DAYS_BETWEEN_SCANS, args=[user])  # pyright: ignore[reportUnknownMemberType]
        scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
            send_heath_check_if_no_change, "interval", days=DAYS_BETWEEN_HEALTH_CHECKS, args=[user]
        )

    scheduler.start()
    yield
    scheduler.shutdown()
