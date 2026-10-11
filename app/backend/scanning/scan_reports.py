import logging
import threading
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from app.backend.email_service.delivery import EmailSender, is_undeliverable
from app.backend.email_service.email_wording import scan_report_subject
from app.backend.email_service.html_bodies import join_scan_reports, scan_report_html
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.scanning.notifications import (
    group_by_recipient,
    send_notifications,
    send_notifications_skipping_failures,
)
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.scan_run_models import ScanRunRead
from app.models.website_models import WebsiteRead

logger: logging.Logger = logging.getLogger(__name__)

# Held while reports are emailed and recorded as emailed, so two runs that finish together (e.g. Run All Scans and a
# scheduled check, or Run Scan Now during a scheduled check) never email the same report twice
REPORT_EMAILS_LOCK: threading.Lock = threading.Lock()


class WebsiteReport(NamedTuple):
    """One scan's report, ready to be emailed to its website's recipients.

    Attributes:
        website: The website the report is about, which says who to email and names it in the subject line.
        scan_run: The scan the report is about.
        html: The report card as HTML.
    """

    website: WebsiteRead
    scan_run: ScanRunRead
    html: str


class ReportEmail(NamedTuple):
    """An email to one recipient, holding the reports of one or more scans.

    Attributes:
        email: The email.
        scan_run_ids: The scans whose reports it holds.
    """

    email: OutgoingEmail
    scan_run_ids: frozenset[uuid.UUID]


def write_report(website: WebsiteRead, scan_run: ScanRunRead) -> WebsiteReport:
    """Writes the report of one scan of a website.

    Args:
        website: The website that was scanned.
        scan_run: The scan.

    Returns:
        The report.
    """
    return WebsiteReport(website, scan_run, scan_report_html(website.url, scan_run))


def _reports_awaiting_email() -> list[WebsiteReport]:
    """Reads every scan report that has not been emailed yet, across all websites.

    Returns:
        The reports, oldest scan first.
    """
    with db_context() as session:
        scan_runs: list[ScanRunRead] = ScanRunService(session).get_awaiting_email()
        website_service = WebsiteService(session)
        websites: dict[uuid.UUID, WebsiteRead] = {
            website_id: website_service.get(website_id)
            for website_id in dict.fromkeys(scan_run.website_id for scan_run in scan_runs)
        }
    return [write_report(websites[scan_run.website_id], scan_run) for scan_run in scan_runs]


def _report_emails(reports: Sequence[WebsiteReport]) -> list[ReportEmail]:
    """Writes one email for each recipient, holding the reports for all of their websites.

    Args:
        reports: The reports to send.

    Returns:
        The emails, one per recipient.
    """
    reports_by_recipient = group_by_recipient(reports, lambda report: report.website.recipients)
    return [
        ReportEmail(
            email=OutgoingEmail(
                to=recipient_email,
                subject=scan_report_subject([str(report.website.url) for report in recipient_reports]),
                html_body=f"<ul>{join_scan_reports(report.html for report in recipient_reports)}</ul>",
            ),
            scan_run_ids=frozenset(report.scan_run.id for report in recipient_reports),
        )
        for recipient_email, recipient_reports in reports_by_recipient.items()
    ]


def _scan_runs_to_retry(
    report_emails: Sequence[ReportEmail], failures: Mapping[OutgoingEmail, Exception]
) -> set[uuid.UUID]:
    """Finds the scans whose report did not reach one of its recipients, but could if it is sent again later.

    Args:
        report_emails: The emails that were sent.
        failures: The emails that could not be sent, with why.

    Returns:
        The IDs of the scans to keep waiting for an email.
    """
    return {
        scan_run_id
        for report_email in report_emails
        if report_email.email in failures and not is_undeliverable(failures[report_email.email])
        for scan_run_id in report_email.scan_run_ids
    }


def record_reports_emailed(scan_run_ids: Collection[uuid.UUID]) -> None:
    """Records that the reports of some scans have been emailed, so they are not sent again.

    Args:
        scan_run_ids: The scans whose reports were emailed.
    """
    with db_context() as session:
        ScanRunService(session).mark_emailed(scan_run_ids, emailed_at=datetime.now(UTC))


def send_reports_awaiting_email(email_sender: EmailSender) -> None:
    """Emails every scan report that has not been emailed yet, one email per recipient holding the reports for all of
    their websites, then records which reports were sent.

    A report that did not reach one of its recipients is kept and sent again the next time this runs (e.g. at the
    next scheduled scan), so changes found while email is down are not lost. An address that is refused, or a mail
    server that rejects the email, is not tried again, as it would fail the same way every time. A report for a
    website with no recipients counts as sent, as there is nobody to send it to.

    Blocks while the emails send, so async code runs it in a thread. Only one run sends at a time, so two runs that
    finish together do not both email the same reports.

    Args:
        email_sender: Sends the emails.
    """
    with REPORT_EMAILS_LOCK:
        reports: list[WebsiteReport] = _reports_awaiting_email()
        if not reports:
            return

        report_emails: list[ReportEmail] = _report_emails(reports)
        failures: dict[OutgoingEmail, Exception] = send_notifications_skipping_failures(
            [report_email.email for report_email in report_emails], email_sender
        )
        scan_runs_to_retry: set[uuid.UUID] = _scan_runs_to_retry(report_emails, failures)
        if scan_runs_to_retry:
            logger.warning("%d scan reports could not be emailed, so they will be sent again.", len(scan_runs_to_retry))
        try:
            record_reports_emailed(
                [report.scan_run.id for report in reports if report.scan_run.id not in scan_runs_to_retry]
            )
        except Exception:
            logger.exception(
                "The scan reports were emailed, but that could not be recorded, so they may be sent again."
            )


def _is_awaiting_email(scan_run_id: uuid.UUID) -> bool:
    """Checks whether a scan's report is still waiting to be emailed.

    Args:
        scan_run_id: The scan.

    Returns:
        True if the report has not been emailed yet.
    """
    with db_context() as session:
        return ScanRunService(session).is_awaiting_email(scan_run_id)


def email_report_once(
    scan_run_id: uuid.UUID,
    emails: Sequence[OutgoingEmail],
    recipient_emails: Collection[str],
    email_sender: EmailSender,
) -> None:
    """Emails one scan's report (e.g. from Run Scan Now), then records it as emailed, without emailing anyone twice.

    A scheduled run that finished while the scan ran may already have emailed the report to the website's
    recipients, in which case only the other addresses are emailed. Blocks while the emails send, so async code runs
    it in a thread.

    Args:
        scan_run_id: The scan whose report is being emailed.
        emails: The report email for each address.
        recipient_emails: The website's recipients, who a scheduled run emails the report to.
        email_sender: Sends the emails.

    Raises:
        smtplib.SMTPException: If an email could not be sent. The report is kept to send again.
        OSError: If the mail server could not be reached. The report is kept to send again.
    """
    with REPORT_EMAILS_LOCK:
        if not _is_awaiting_email(scan_run_id):
            emails = [email for email in emails if email.to not in recipient_emails]
        send_notifications(emails, email_sender)
        record_reports_emailed([scan_run_id])
