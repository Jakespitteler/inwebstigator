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
from app.scanner import scan_user_websites

logger = logging.getLogger(__name__)

HOURS_BETWEEN_SCANS: int = config.hours_between_scans
ADMIN_ID: uuid.UUID = uuid.uuid4()

scheduler = AsyncIOScheduler()
db_context = contextmanager(get_db_session)


async def scan_with_fresh_db_session(user_id: uuid.UUID):
    """Wrapper to instantiate a fresh database session for each scheduled run."""
    with db_context() as session:
        await scan_user_websites(session, UserService(session).get(user_id))


@asynccontextmanager
async def schedule_scans(app: FastAPI) -> AsyncGenerator[None]:
    if not config.user_id:
        config.user_id = ADMIN_ID

    with db_context() as session:
        users = UserService(session).get_all()
        for user in users:
            if not user.last_scan_at or (datetime.now() - user.last_scan_at) > timedelta(hours=HOURS_BETWEEN_SCANS):
                logger.info("Missed scan interval detected. Running scan immediately on startup...")
                await scan_user_websites(session, user)

    for user in users:
        scheduler.add_job(scan_with_fresh_db_session, "interval", hours=HOURS_BETWEEN_SCANS, args=[user.id])  # pyright: ignore[reportUnknownMemberType]

    scheduler.start()
    yield
    scheduler.shutdown()
