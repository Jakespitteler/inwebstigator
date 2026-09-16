import logging
from email.message import EmailMessage

from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.backend.email_service import build_message, send_email
from app.backend.engine import get_website_updates
from app.backend.format_message import generate_scan_report_html
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

EMAIL: str = config.email
APP_PASSWORD: SecretStr = config.email_password


async def scan_website(
    client: AsyncClient,
    session: Session,
    website: WebsiteRead,
    recipient_email: str | None = None,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str:
    # Scan website
    try:
        website_updates: WebsiteUpdate = await get_website_updates(client, website, max_pages, delay, concurrent)
        updated_website: WebsiteRead = WebsiteService(session).update(id=website.id, model_update=website_updates)
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
    scan_report_body: str = generate_scan_report_html(updated_website)

    # Send notification
    if not recipient_email:
        recipient_email = UserService(session).get(id=website.user_id).email

    msg: EmailMessage = build_message(
        subject="Website Update",
        recipients=[recipient_email],
        html_body=scan_report_body,
    )
    try:
        send_email(msg)
    except Exception as e:
        logger.error(f"Sending the update email to {recipient_email} failed: {e}")
        # TODO Decide what to do when email fails (db doesn't roll back rn)
        return "Email failed to send."

    return scan_report_body
