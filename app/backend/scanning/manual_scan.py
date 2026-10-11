import asyncio
import smtplib
from datetime import UTC, datetime

from pydantic import HttpUrl

from app.backend.crawler.page_fetcher import new_http_client
from app.backend.email_service.delivery import EmailSender
from app.backend.email_service.email_wording import manual_scan_subject
from app.backend.email_service.html_bodies import scan_report_html
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.scanning.scan_reports import email_report_once
from app.backend.scanning.website_scan import scan_website
from app.core.errors import ReportNotEmailedError
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.scan_run_models import ScanRunRead
from app.models.website_models import WebsiteRead, WebsiteUpdate


def _report_recipients(website: WebsiteRead, extra_email: str | None) -> list[str]:
    """Lists who to email a website's report to: its recipients, and anyone else asked for, each only once.

    Args:
        website: The website that was scanned.
        extra_email: Another address to send the report to, or None.

    Returns:
        The email addresses.
    """
    recipient_emails: list[str] = [recipient.email for recipient in website.recipients]
    return list(dict.fromkeys([*recipient_emails, *([extra_email] if extra_email else [])]))


async def scan_website_now(
    url: HttpUrl,
    email_sender: EmailSender,
    extra_email: str | None = None,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str | None:
    """Scans a website straight away (for "Run Scan Now"), then emails its report to the website's recipients and to
    any other address asked for.

    Once emailed, the report is recorded as sent. If the email fails, the report is kept and sent with the next
    scheduled scan.

    Args:
        url: The URL of the website, which must already be monitored.
        email_sender: Sends the report.
        extra_email: Another address to send the report to, or None.
        max_pages: The most pages to crawl, or None for the default.
        delay: Seconds to wait between requests, or None for the website's own.
        concurrent: The most requests at once, or None for the website's own.

    Returns:
        The report as HTML if changes were found or the scan ran into a problem, otherwise None.

    Raises:
        NotFoundError: If the website is not monitored.
        TrafficError: If the website rate limited a scan that was given its own delay or concurrency.
        ScanAlreadyQueuedError: If the website is already queued or being scanned.
        ScanCancelledError: If the scan was cancelled before it finished.
        ReportNotEmailedError: If the scan finished but its report could not be emailed (e.g. the mail server could
            not be reached). The report is kept to send with the next scheduled scan.
    """
    with db_context() as session:
        website: WebsiteRead = WebsiteService(session).get_by_url(url)

    async with new_http_client() as client:
        scan_run: ScanRunRead = await scan_website(client, website, max_pages, delay, concurrent)

    with db_context() as session:
        WebsiteService(session).update(id=website.id, model_update=WebsiteUpdate(last_scan_at=datetime.now(UTC)))

    if not scan_run.has_report:
        return None

    report: str = scan_report_html(website.url, scan_run)
    recipient_emails: list[str] = _report_recipients(website, extra_email)
    if recipient_emails:
        subject: str = manual_scan_subject(str(website.url))
        emails = [OutgoingEmail(to=email, subject=subject, html_body=report) for email in recipient_emails]
        website_recipients: set[str] = {recipient.email for recipient in website.recipients}
        try:
            await asyncio.to_thread(email_report_once, scan_run.id, emails, website_recipients, email_sender)
        except (smtplib.SMTPException, OSError) as error:
            raise ReportNotEmailedError(str(website.url)) from error
    return report
