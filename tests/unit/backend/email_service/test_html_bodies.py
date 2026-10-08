from bs4 import BeautifulSoup
from pydantic import HttpUrl

from app.backend.email_service.email_wording import WebsiteHealth
from app.backend.email_service.html_bodies import (
    ScanStatus,
    generate_scan_report_html,
    health_check_html,
    join_scan_reports,
    monitoring_started_html,
    recipient_added_html,
    report_divider,
)
from app.models.website_models import WebsiteCreate
from tests.unit.backend.email_service.builders import make_block, make_change, make_page, make_website


def visible_text(html: str) -> str:
    """Reads an email the way a person sees it, as one line of text."""
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())


def list_items(html: str) -> list[str]:
    """Reads each bullet point of an email the way a person sees it."""
    return [" ".join(item.get_text().split()) for item in BeautifulSoup(html, "html.parser").select("li")]


def test_report_without_changes_says_so() -> None:
    report: str = generate_scan_report_html(make_website())

    assert "Website monitoring report for https://example.gov.au/" in visible_text(report)
    assert "No changes detected since the last scan." in visible_text(report)


def test_report_for_a_failed_scan_shows_the_message_in_a_red_alert() -> None:
    report: str = generate_scan_report_html(make_website(), ScanStatus.CONNECTION_ERROR, "Site unreachable.")

    assert "Connection Failure Report" in visible_text(report)
    assert "Site unreachable." in visible_text(report)
    assert "color: #cf222e" in report
    assert "No changes detected" not in report


def test_report_for_a_skipped_scan_uses_the_warning_colour() -> None:
    report: str = generate_scan_report_html(make_website(), ScanStatus.TOO_LARGE, "Too many pages.")

    assert "Scan Refused (Website Too Large)" in visible_text(report)
    assert "color: #9a6700" in report


def test_report_escapes_text_from_the_website() -> None:
    """Tests a heading or paragraph from a website cannot add its own HTML to the email."""
    page = make_page(recent_text_added=[make_block("<script>alert(1)</script>", heading="<b>Fees</b>")])

    report: str = generate_scan_report_html(make_website(critical_pages=[page]))

    assert "<script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "&lt;b&gt;Fees&lt;/b&gt;" in report


def test_report_lists_each_changed_page_once() -> None:
    page = make_page(recent_links_added=["https://example.gov.au/new"])

    report: str = generate_scan_report_html(make_website(critical_pages=[page]))

    assert "Watched Pages Changed (1)" in visible_text(report)
    assert report.count('href="https://example.gov.au/fees"') == 1
    assert "+ https://example.gov.au/new" in visible_text(report)


def test_report_highlights_only_the_words_that_changed() -> None:
    edit = make_change(
        make_block("The application fee is $50 per year."), make_block("The application fee is $60 per year.")
    )
    page = make_page(recent_text_changed=[edit])

    report: str = generate_scan_report_html(make_website(critical_pages=[page]))
    highlighted: list[str] = [span.get_text() for span in BeautifulSoup(report, "html.parser").select("td span")]

    assert highlighted == ["$50", "$60"]


def test_report_lists_text_that_moved_to_another_section() -> None:
    move = make_change(make_block("Closed", heading="Saturday"), make_block("Closed", heading="Sunday"))
    page = make_page(recent_text_changed=[move])

    report: str = generate_scan_report_html(make_website(critical_pages=[page]))

    assert "Text Moved:" in visible_text(report)
    assert "“Closed” moved from Saturday to Sunday" in list_items(report)
    assert "Text Changed:" not in visible_text(report)


def test_report_lists_unreachable_pages_instead_of_their_old_changes() -> None:
    page = make_page(consecutive_failures=2, last_failure_reason="HTTP 500", recent_text_added=[make_block("Old")])

    report: str = generate_scan_report_html(make_website(critical_pages=[page]))

    assert "Watched Pages Unreachable (1)" in visible_text(report)
    assert "HTTP 500 (2 scans in a row)" in visible_text(report)
    assert "Watched Pages Changed" not in visible_text(report)


def test_join_scan_reports_puts_a_line_between_each_card() -> None:
    joined: str = join_scan_reports(["<div>one</div>", "<div>two</div>", "<div>three</div>"])

    assert joined.count(report_divider()) == 2


def test_recipient_added_names_the_website() -> None:
    body: str = recipient_added_html("https://example.gov.au/?a=1&b=2")

    assert "being added as a recipient for https://example.gov.au/?a=1&b=2" in visible_text(body)
    assert "a=1&amp;b=2" in body


def test_health_check_says_all_is_well_when_nothing_needs_attention() -> None:
    body: str = health_check_html([WebsiteHealth(HttpUrl("https://a.example/"), "Working.", needs_attention=False)])

    assert "Monitoring is running, and no changes have been found" in visible_text(body)
    assert list_items(body) == ["https://a.example/: Working."]


def test_health_check_says_when_a_website_needs_attention() -> None:
    body: str = health_check_html(
        [
            WebsiteHealth(HttpUrl("https://a.example/"), "Working.", needs_attention=False),
            WebsiteHealth(HttpUrl("https://b.example/"), "2 watched pages cannot be reached.", needs_attention=True),
        ]
    )

    assert "Some of your websites are not being fully monitored." in visible_text(body)
    assert list_items(body) == [
        "https://a.example/: Working.",
        "https://b.example/: 2 watched pages cannot be reached.",
    ]


def test_monitoring_started_html_lists_the_main_page_and_critical_pages():
    """Tests the email lists the website's main page, which is always watched, then its other critical pages,
    each only once (here "/" is the main page again)."""
    body = monitoring_started_html(WebsiteCreate(url=HttpUrl("https://example.com"), critical_pages=["/news", "/"]), 7)

    assert body.count("<li") == 2
    assert body.index("https://example.com/<") < body.index("https://example.com/news<")


def test_monitoring_started_html_escapes_urls_and_describes_schedule():
    """Tests the email body escapes URLs and states the scan and health check intervals."""
    website = WebsiteCreate(url=HttpUrl("https://example.com/?a=1&b=<script>"), days_between_scans=1)

    body = monitoring_started_html(website, 7)

    assert "https://example.com/?a=1&amp;b=%3Cscript%3E" in body  # pydantic percent-encodes the "<" and ">"
    assert "<script>" not in body
    assert "checked every day" in body
    assert "every 7 days" in body
