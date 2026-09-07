import asyncio
import logging

from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.backend.errors import TrafficError, WebConnectionError
from app.backend.notifications.email_service import generate_scan_report_body, send_email
from app.backend.web_scraper.engine import get_website_state
from app.core.config import config
from app.db.models.internal_link_models import InternalLinkCreateBatch, InternalLinkRead
from app.db.models.website_models import WebsiteRead, WebsiteState
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService

logger = logging.getLogger(__name__)

EMAIL: str = config.email
APP_PASSWORD: SecretStr = config.email_password


def _update_database_website_state(session: Session, website_state: WebsiteState):
    # TODO If any below fail then we may need to rollback database change and do something else
    # Update critical pages
    critical_page_service = CriticalPageService(session)
    for critical_page in website_state.critical_page_states:
        critical_page_service.update(id=critical_page.id, model_update=critical_page.updates)

    # Sync internal links
    internal_link_service = InternalLinkService(session)
    if website_state.added_internal_links:
        internal_link_service.create_batch(
            InternalLinkCreateBatch(urls=website_state.added_internal_links, website_id=website_state.id)
        )
    if website_state.removed_internal_links:
        for link in website_state.removed_internal_links:
            internal_link: InternalLinkRead = internal_link_service.get_by_url(url=link)
            internal_link_service.delete(id=internal_link.id)


async def scan_website(
    client: AsyncClient,
    session: Session,
    website: WebsiteRead,
    recipient_email: str,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str:
    # Scan website
    try:
        website_state: WebsiteState = await get_website_state(client, website, max_pages, delay, concurrent)

    except TrafficError as e:  # TODO Make sure we are actually catching all of the errors and reacting accordingly
        logger.error(f"Too many requests for website, waiting and reducing speed then trying again. {e}")
        WebsiteService(session).throttle_crawler(id=website.id)
        await asyncio.sleep(1 * 60)  # TODO  maybe test a fetch and wait till open
        return await scan_website(client, session, website, recipient_email, max_pages)

    except WebConnectionError as e:
        logger.error(f"Lost connection, waiting and trying again. {e}")
        await asyncio.sleep(1 * 60)  # TODO  maybe test a fetch and wait till open
        return await scan_website(client, session, website, recipient_email, max_pages, delay, concurrent)

    # Format notification
    scan_report_body: str = generate_scan_report_body(website_state)

    # Update database
    _update_database_website_state(session, website_state)

    # Send notification
    send_email(
        email=EMAIL,
        app_password=APP_PASSWORD,
        recipient_email=recipient_email,
        subject="Website Update",
        body=scan_report_body,
    )
    return scan_report_body
