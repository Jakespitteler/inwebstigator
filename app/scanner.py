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
from app.db.utils.field_types import EmailString
from app.models import user_models
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

EMAIL: str = config.email
APP_PASSWORD: SecretStr = config.email_password


def send_notification(recipient_email: EmailString, report: str):
    """
    Builds and sends an HTML email notification containing a website scan report.

    Args:
        recipient_email (EmailString): The email address of the recipient.
        report (str): The HTML formatted scan report to include in the email body.

    Returns:
        str | None: Returns an error message string if the email fails to send,
        otherwise returns None on success.
    """

    msg: EmailMessage = build_message(
        subject="Website Update",
        recipients=[recipient_email],
        html_body=report,
    )
    try:
        send_email(msg)
    except Exception as e:
        logger.error(f"Sending the update email to {recipient_email} failed: {e}")
        # TODO Decide what to do when email fails (db doesn't roll back rn)
        return "Email failed to send."


async def scan_website(
    client: AsyncClient,
    session: Session,
    website: WebsiteRead,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str:
    """
    Asynchronously scans a website for updates, updates the database record, and
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
        ScanResult: A result container holding an `html_report` string if successful,
        or a `fail_message` string if the scan was aborted due to traffic or connection issues.
    """
    website_service = WebsiteService(session)
    try:
        website_updates: WebsiteUpdate = await get_website_updates(client, website, max_pages, delay, concurrent)
        updated_website: WebsiteRead = website_service.update(
            id=website.id,
            model_update=website_updates,
        )
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

    website_service.reset_failed_attempts(website.id)
    scan_report_body: str = generate_scan_report_html(updated_website, status=ScanStatus.SUCCESS)

    return scan_report_body


async def scan_user_websites(session: Session, user: user_models.UserRead) -> str:
    reports: list[str] = []
    async with AsyncClient() as client:
        for website in user.websites:
            if not website.active:
                reports.append(
                    generate_scan_report_html(
                        website,
                        status=ScanStatus.SKIPPED_DEACTIVATED,
                        message="Website has been deactivated due to consecutive scan failures.",
                    )
                )
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                reports.append(
                    generate_scan_report_html(
                        website,
                        status=ScanStatus.SKIPPED_COOLDOWN,
                        message=f"Cooldown active until {website.on_cooldown_until:%d %b %Y, %H:%M UTC}.",
                    )
                )
                continue

            reports.append(await scan_website(client, session, website))
    joint_reports = f"<ul>{''.join(reports)}</ul>"

    send_notification(user.email, joint_reports)
    UserService(session).update(id=user.id, model_update=user_models.UserUpdate(last_scan_at=datetime.now()))
    return joint_reports
