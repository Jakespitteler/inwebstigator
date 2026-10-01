import html
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from email.message import EmailMessage

from httpx2 import AsyncClient
from pydantic import SecretStr

from app.backend.email_service import build_message, send_email
from app.backend.engine import get_website_updates
from app.backend.format_message import ScanStatus, generate_scan_report_html
from app.core.config import config
from app.core.errors import TrafficError, WebConnectionError
from app.db.core import db_context
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.utils.field_types import EmailString
from app.models.recipient_models import RecipientUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

EMAIL: str = config.email
APP_PASSWORD: SecretStr = config.email_password


def send_notification(recipient_email: EmailString, report: str, subject: str = "Website Update"):
    """Builds and sends an HTML email notification containing a website scan report
    and updates the recipient's `last_email_at` timestamp.

    Args:
        recipient (RecipientRead): The target recipient recipient.
        report (str): The HTML formatted scan report to include in the email body.
        subject (str, optional): The email subject line. Defaults to "Website Update".
    """

    msg: EmailMessage = build_message(
        subject=subject,
        recipients=[recipient_email],
        html_body=report,
    )
    send_email(msg)

    with db_context() as session:
        recipient_service = RecipientService(session)
        recipient = recipient_service.get_by_email(recipient_email)
        recipient_service.update(id=recipient.id, model_update=RecipientUpdate(last_email_at=datetime.now()))


def _format_days(days: float) -> str:
    """Formats a day count for display, e.g. 1 -> "day", 7 -> "7 days", 0.5 -> "0.5 days"."""
    return "day" if days == 1 else f"{days:g} days"


def _monitoring_started_html(website: WebsiteRead, days_between_health_checks: float) -> str:
    """Generates the HTML body of the email confirming a website is now being monitored.

    Args:
        website (WebsiteRead): The website that has started being monitored.
        days_between_health_checks (float): How often the recipient is told nothing has changed.
    """
    critical_pages = "".join(
        f'<li style="margin-bottom: 4px;">{html.escape(critical_page.url)}</li>'
        for critical_page in website.critical_pages
    )
    critical_pages_section = (
        f'<p style="margin: 16px 0 6px;">Pages being watched for content changes:</p>'
        f'<ul style="padding-left: 20px; margin: 0;">{critical_pages}</ul>'
        if critical_pages
        else ""
    )
    return (
        "<div style=\"font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; "
        "background-color: #ffffff; color: #1f2328; line-height: 1.5; padding: 20px; max-width: 800px; "
        'margin: 0 auto; border-radius: 8px; border: 1px solid #d0d7de;">'
        '<h2 style="margin-top: 0; font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 8px;">'
        f"Now monitoring {html.escape(website.url)}</h2>"
        '<p style="margin: 0;">You have been added as a recipient for this website. '
        f"It is checked every {_format_days(website.days_between_scans)}, and you will be emailed "
        "when a change is found.</p>"
        f"{critical_pages_section}"
        '<p style="margin: 16px 0 0;">If nothing changes, you will get a short confirmation email every '
        f"{_format_days(days_between_health_checks)} so you know monitoring is still running.</p>"
        '<hr style="border: 0; height: 1px; background: #d0d7de; margin: 25px 0;">'
        '<p style="color: #57606a; font-size: 12px; margin: 0;">This is an automated message.</p>'
        "</div>"
    )


def send_monitoring_started_notifications(website: WebsiteRead) -> None:
    """Emails each of a website's recipients to confirm the website is now being monitored.

    A failed send is logged and skipped, so it cannot fail the request that added the website
    or stop the remaining recipients being emailed.

    Args:
        website (WebsiteRead): The website that has started being monitored.
    """
    for recipient in website.recipients:
        try:
            send_notification(
                recipient.email,
                _monitoring_started_html(website, recipient.days_between_health_checks),
                subject="Website monitoring started",
            )
        except Exception:
            logger.exception(f"Failed to send monitoring started email to {recipient.email}")


async def scan_website(
    client: AsyncClient,
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
    try:
        # Backfill websites added before main URLs were monitored automatically.
        if not any(page.url == website.url for page in website.critical_pages):
            with db_context() as session:
                website = WebsiteService(session).ensure_main_critical_page(website.id)

        website_updates: WebsiteUpdate | None = await get_website_updates(client, website, max_pages, delay, concurrent)
        if website_updates:
            with db_context() as session:
                website_service = WebsiteService(session)
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

        with db_context() as session:
            action_message: str = WebsiteService(session).handle_traffic_error(website)
        return generate_scan_report_html(website, status=ScanStatus.TRAFFIC_ERROR, message=action_message)

    except WebConnectionError as e:
        logger.error(f"Site unreachable: {e}")
        with db_context() as session:
            action_message: str = WebsiteService(session).handle_connection_error(website.id)
        return generate_scan_report_html(website, status=ScanStatus.CONNECTION_ERROR, message=action_message)


async def scan_all_websites() -> str | None:
    """Asynchronously scans all active, non-cooldown websites registered to a recipient.

    Updates the recipient's `last_scan_at` metadata and dispatches an HTML email notification
    if any scan reports were generated.

    Returns:
        str | None: Consolidated HTML list of scan reports if updates/errors occurred,
        otherwise None.
    """

    with db_context() as session:
        website_service = WebsiteService(session)
        websites = website_service.get_all()

    reports: dict[EmailString, list[str]] = defaultdict(list)
    all_reports_html: str = ""
    async with AsyncClient() as client:
        for website in websites:
            if not website.active:
                logger.warning(f"{website.url} has been skipped as it has been deactivated.")
                continue
            if website.on_cooldown_until and website.on_cooldown_until > datetime.now():
                logger.warning(f"{website.url} has been skipped as it is on cooldown.")
                continue
            if website.last_scan_at and (datetime.now() - website.last_scan_at) < timedelta(
                days=website.days_between_scans
            ):
                logger.info(f"{website.url} has been skipped as there has not been enough time since last scan.")
                continue

            report: str | None = await scan_website(client, website)

            if report:
                for recipient in website.recipients:
                    reports[recipient.email].append(report)
                all_reports_html += report
            else:
                logger.info(f"No updates found for: {website.url}")

            with db_context() as session:
                WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=datetime.now()))

    if reports:
        for recipient_email, recipient_report in reports.items():
            send_notification(recipient_email, f"<ul>{''.join(recipient_report)}</ul>")
        return all_reports_html
