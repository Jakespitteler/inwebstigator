import logging
from datetime import datetime
from email.message import EmailMessage

from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.backend.email_service import build_message, send_email
from app.backend.engine import get_website_updates
from app.backend.format_message import ScanStatus, generate_scan_report_html
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.models.user_models import UserRead, UserUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

EMAIL: str = config.email
APP_PASSWORD: SecretStr = config.email_password


def send_notification(session: Session, user: UserRead, report: str):
    """Builds and sends an HTML email notification containing a website scan report
    and updates the user's `last_email_at` timestamp.

    Args:
        session (Session): The SQLAlchemy database session.
        user (UserRead): The target user recipient.
        report (str): The HTML formatted scan report to include in the email body.
    """

    msg: EmailMessage = build_message(
        subject="Website Update",
        recipients=[user.email],
        html_body=report,
    )
    send_email(msg)
    UserService(session).update(id=user.id, model_update=UserUpdate(last_email_at=datetime.now()))


async def scan_website(
    client: AsyncClient,
    session: Session,
    website: WebsiteRead,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str | None:
    """Asynchronously scans a website for updates, updates the database record, and
    generates an HTML scan report.

    Automatically handles rate limits and unreachable sites by applying database
    cooldown periods to the affected website record.

    Args:
        client (AsyncClient): The HTTPX asynchronous client for making web requests.
        session (Session): The SQLAlchemy database session.
        website (WebsiteRead): The website database record to scan.
        max_pages (int | None, optional): The maximum number of pages to crawl. Defaults to None.
        delay (float | None, optional): Time in seconds to wait between requests. Defaults to None.
        concurrent (int | None, optional): Maximum number of concurrent connections. Defaults to None.

    Returns:
        str | None: An HTML scan report string if updates or errors were recorded,
        otherwise None if no changes were found.

    Raises:
        TrafficError: Re-raised if custom delay/concurrent parameters were set during a rate-limited scan.
    """
    website_service = WebsiteService(session)
    try:
        website_updates: WebsiteUpdate | None = await get_website_updates(client, website, max_pages, delay, concurrent)
        if website_updates:
            updated_website: WebsiteRead = website_service.update(
                id=website.id,
                model_update=website_updates,
            )
            website_service.reset_failed_attempts(website.id)
            return generate_scan_report_html(updated_website, status=ScanStatus.SUCCESS)
    except TrafficError as e:
        logger.error(f"Temporary ban or severe rate limit detected for {website.url}: {e}")
        if delay or concurrent:
            raise TrafficError(
                url=website.url,
                status_code=e.status_code,
                message="Scan aborted, try increasing delay or reducing concurrent (may be banned)",
            ) from e

        action_message: str = website_service.handle_traffic_error(website)
        return generate_scan_report_html(website, status=ScanStatus.TRAFFIC_ERROR, message=action_message)

    except WebConnectionError as e:
        logger.error(f"Site unreachable: {e}")
        action_message: str = website_service.handle_connection_error(website.id)
        return generate_scan_report_html(website, status=ScanStatus.CONNECTION_ERROR, message=action_message)


async def scan_user_websites(session: Session, user: UserRead) -> str | None:
    """Asynchronously scans all active, non-cooldown websites registered to a user.

    Updates the user's `last_scan_at` metadata and dispatches an HTML email notification
    if any scan reports were generated.

    Args:
        session (Session): The SQLAlchemy database session.
        user (UserRead): The user whose registered websites will be scanned.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates/errors occurred,
        otherwise None.
    """
    reports: list[str] = []
    async with AsyncClient() as client:
        for website in user.websites:
            if not website.active:
                logger.warning(f"{website.url} has been skipped as it has been deactivated.")
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                logger.warning(f"{website.url} has been skipped as it is on cooldown.")
                continue

            report: str | None = await scan_website(client, session, website)

            if report:
                reports.append(report)
            else:
                logger.info(f"No updates found for: {website.url}")

    user_service = UserService(session)
    user_service.update(id=user.id, model_update=UserUpdate(last_scan_at=datetime.now()))
    if reports:
        joint_reports = f"<ul>{''.join(reports)}</ul>"
        send_notification(session, user, joint_reports)
        return joint_reports
