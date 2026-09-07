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
    except TrafficError as e:
        logger.error(f"Temporary ban or severe rate limit detected for {website.url}: {e}")
        if delay or concurrent:
            return "Scan aborted, try increasing delay or reducing concurrent (may be banned)"
        WebsiteService(session).throttle_and_cooldown(id=website.id, hours=24)
        return "Scan aborted due to traffic issues and website placed on cooldown."
    except WebConnectionError as e:
        logger.error(f"Site unreachable: {e}")
        WebsiteService(session).set_cooldown(id=website.id, hours=2)
        return "Scan aborted due to connection issues and website placed on cooldown."

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
