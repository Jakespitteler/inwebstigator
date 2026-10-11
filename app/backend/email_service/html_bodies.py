from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from typing import NamedTuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pydantic import HttpUrl

from app.backend.diff_checker.word_diff import diff_words
from app.backend.email_service.email_wording import WebsiteHealth, format_email_time
from app.backend.email_service.style_inliner import inline_styles
from app.core.config import config
from app.core.paths import resource_path
from app.core.urls import remove_repeated_pages
from app.models.scan_run_models import PageChanges, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteCreate


class Severity(StrEnum):
    """How serious a scan problem is, which decides the colour of the report's title and alert box."""

    ERROR = "error"
    WARNING = "warning"


class StatusStyle(NamedTuple):
    """The title and severity of a report for one scan status.

    Attributes:
        title: The report's title, e.g. "Connection Failure Report".
        severity: How serious the problem is, or None for a normal scan, which has no alert box.
    """

    title: str
    severity: Severity | None


STATUS_STYLES: dict[ScanStatus, StatusStyle] = {
    ScanStatus.SUCCESS: StatusStyle("Website monitoring report", None),
    ScanStatus.TRAFFIC_ERROR: StatusStyle("Traffic / Rate Limit Error", Severity.ERROR),
    ScanStatus.CONNECTION_ERROR: StatusStyle("Connection Failure Report", Severity.ERROR),
    ScanStatus.SCAN_ERROR: StatusStyle("Scan Failure Report", Severity.ERROR),
    ScanStatus.SKIPPED_DEACTIVATED: StatusStyle("Scan Skipped (Deactivated)", Severity.WARNING),
    ScanStatus.SKIPPED_COOLDOWN: StatusStyle("Scan Skipped (Cooldown Active)", Severity.WARNING),
    ScanStatus.TOO_LARGE: StatusStyle("Scan Refused (Website Too Large)", Severity.WARNING),
    ScanStatus.PAGES_MISSING: StatusStyle("Most Pages Missing", Severity.WARNING),
}


@cache
def _templates() -> Environment:
    """Loads the email templates once, the first time an email is written.

    The templates style their tags with classes from `email.css`, which are copied onto the tags before sending, as
    email apps ignore linked stylesheets and `<style>` blocks. Autoescaping makes any text
    from a website (a URL, a heading, a changed paragraph) safe to put in the email.

    Returns:
        The Jinja environment for the email templates.
    """
    return Environment(
        loader=FileSystemLoader(resource_path("app", "backend", "email_service", "templates")),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render(template_name: str, **values: object) -> str:
    """Writes an email body from one of the templates.

    Args:
        template_name: The template's file name, e.g. "scan_report.html".
        **values: The values the template shows. The title is the default colour unless `severity` is given.

    Returns:
        The email body as HTML.
    """
    html: str = _templates().get_template(template_name).render({"severity": None, **values})
    return inline_styles(html)


def generate_scan_report_html(website_url: HttpUrl | str, scan_run: ScanRunRead) -> str:
    """Generates the inline-styled HTML report card for one scan of a website, for any scan status.

    Long lists of links are cut short (see `config.email_max_listed_links`), as a scan that finds thousands of new
    pages would otherwise make an email too large to arrive whole. The dashboard lists them all.

    Args:
        website_url: The website that was scanned.
        scan_run: The scan, with only the changes it found.

    Returns:
        The report card as HTML.
    """
    pages: list[PageChanges] = scan_run.pages
    style: StatusStyle = STATUS_STYLES[scan_run.status]
    return _render(
        "scan_report.html",
        severity=style.severity,
        website_url=website_url,
        scan_run=scan_run,
        style=style,
        is_success=scan_run.status is ScanStatus.SUCCESS,
        message=scan_run.message or "No further details available.",
        checked_at=format_email_time(scan_run.scanned_at),
        has_changes=bool(scan_run.changes),
        unreachable_pages=[page for page in pages if page.is_unreachable],
        changed_pages=[page for page in pages if not page.is_unreachable],
        diff_words=diff_words,
        max_listed_links=config.email_max_listed_links,
    )


@cache
def report_divider() -> str:
    """Writes the line that goes between the report cards of an email.

    Returns:
        The line as inline-styled HTML.
    """
    return inline_styles('<hr class="report-divider">')


def join_scan_reports(reports: Iterable[str]) -> str:
    """Joins scan report cards into one email body, with a line between each card.

    Args:
        reports: The HTML scan reports, in the order they should appear.

    Returns:
        The reports as one HTML string.
    """
    return report_divider().join(reports)


def recipient_added_html(website_url: str) -> str:
    """Generates the HTML body of the email sent to confirm an address can receive email when it is added.

    Args:
        website_url: The URL of the website the address is being added as a recipient for.

    Returns:
        The email body as HTML.
    """
    return _render("recipient_added.html", website_url=website_url)


def _format_days(days: float) -> str:
    """Formats a day count for display, e.g. 1 -> "day", 7 -> "7 days", 0.5 -> "0.5 days".

    Args:
        days: The number of days.

    Returns:
        The count as words.
    """
    return "day" if days == 1 else f"{days:g} days"


def monitoring_started_html(website: WebsiteCreate, days_between_health_checks: float) -> str:
    """Generates the HTML body of the one email sent to each recipient of a website being added, which says
    what is monitored and also confirms their address can receive email.

    Args:
        website: The website being added. Its main page is always watched, so it is listed first.
        days_between_health_checks: How often the recipient is told nothing has changed.

    Returns:
        The email body as HTML.
    """
    return _render(
        "monitoring_started.html",
        website=website,
        scan_interval=_format_days(website.days_between_scans),
        watched_pages=remove_repeated_pages([str(website.url), *website.critical_pages]),
        health_check_interval=_format_days(days_between_health_checks),
    )


def health_check_html(website_healths: Sequence[WebsiteHealth]) -> str:
    """Generates the HTML body of a health check email, which lists how each of the recipient's websites is doing.

    Args:
        website_healths: How monitoring is going for each of the recipient's websites.

    Returns:
        The email body as HTML.
    """
    return _render(
        "health_check.html",
        sent_at=format_email_time(datetime.now(UTC)),
        needs_attention=any(health.needs_attention for health in website_healths),
        website_healths=website_healths,
    )
