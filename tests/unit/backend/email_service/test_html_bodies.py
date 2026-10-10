from datetime import datetime

import pytest
from bs4 import BeautifulSoup
from pydantic import HttpUrl
from pytest_mock import MockerFixture

from app.backend.email_service.email_wording import WebsiteHealth
from app.backend.email_service.html_bodies import (
    STATUS_STYLES,
    generate_scan_report_html,
    health_check_html,
    join_scan_reports,
    monitoring_started_html,
    recipient_added_html,
    report_divider,
)
from app.core.config import config
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanStatus
from app.models.website_models import WebsiteCreate
from tests.unit.backend.email_service.builders import PAGE_URL, WEBSITE_URL, make_block, make_change, make_scan_run


def visible_text(html: str) -> str:
    """Reads an email the way a person sees it, as one line of text."""
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())


def list_items(html: str) -> list[str]:
    """Reads each bullet point of an email the way a person sees it."""
    return [" ".join(item.get_text().split()) for item in BeautifulSoup(html, "html.parser").select("li")]


def test_report_without_changes_says_so() -> None:
    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run())

    assert "Website monitoring report for https://example.gov.au/" in visible_text(report)
    assert "No changes detected since the last scan." in visible_text(report)


def test_report_shows_when_the_scan_ran_not_when_it_was_emailed() -> None:
    """Tests a report sent late (e.g. because the mail server was down) still says when the website was checked."""
    report: str = generate_scan_report_html(
        WEBSITE_URL, make_scan_run(scanned_at=datetime(2026, 10, 1, 9, 30).astimezone())
    )

    assert "Checked 01 Oct 2026, 09:30" in visible_text(report)


def test_report_for_a_failed_scan_shows_the_message_in_a_red_alert() -> None:
    report: str = generate_scan_report_html(
        WEBSITE_URL, make_scan_run(ScanStatus.CONNECTION_ERROR, "Site unreachable.")
    )

    alert = BeautifulSoup(report, "html.parser").select_one(".alert")

    assert "Connection Failure Report" in visible_text(report)
    assert alert is not None and "Site unreachable." in alert.get_text()
    assert "background-color: #ffeceb" in str(alert.get("style"))
    assert "color: #cf222e" in report
    assert "No changes detected" not in report


def test_report_for_a_skipped_scan_uses_the_warning_colour() -> None:
    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(ScanStatus.TOO_LARGE, "Too many pages."))

    assert "Scan Refused (Website Too Large)" in visible_text(report)
    assert "color: #9a6700" in report


def test_report_for_a_website_found_too_large_also_lists_its_critical_page_changes() -> None:
    """Tests the scan that deactivates a website still reports the changes found on its critical pages, in the same
    card as the warning."""
    added = ChangeCreate(kind=ChangeKind.TEXT_ADDED, page_url=HttpUrl(PAGE_URL), new_block=make_block("Fee is $60."))

    report: str = generate_scan_report_html(
        WEBSITE_URL, make_scan_run(ScanStatus.TOO_LARGE, "Too many pages.", changes=[added])
    )

    assert "Too many pages." in visible_text(report)
    assert "Watched Pages Changed (1)" in visible_text(report)
    assert "Fee is $60." in visible_text(report)


def test_report_escapes_text_from_the_website() -> None:
    """Tests a heading or paragraph from a website cannot add its own HTML to the email."""
    added = ChangeCreate(
        kind=ChangeKind.TEXT_ADDED,
        page_url=HttpUrl(PAGE_URL),
        new_block=make_block("<script>alert(1)</script>", heading="<b>Fees</b>"),
    )

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[added]))

    assert "<script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "&lt;b&gt;Fees&lt;/b&gt;" in report


def test_report_lists_each_changed_page_once() -> None:
    link_added = ChangeCreate(
        kind=ChangeKind.LINK_ADDED, page_url=HttpUrl(PAGE_URL), url=HttpUrl("https://example.gov.au/new")
    )

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[link_added, link_added]))

    assert "Watched Pages Changed (1)" in visible_text(report)
    assert report.count('href="https://example.gov.au/fees"') == 1
    assert "+ https://example.gov.au/new" in visible_text(report)


def test_report_lists_new_and_removed_internal_links() -> None:
    changes = [
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl("https://example.gov.au/new")),
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_REMOVED, url=HttpUrl("https://example.gov.au/old")),
    ]

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=changes))

    assert "New Internal Links (1)" in visible_text(report)
    assert "Removed Internal Links (1)" in visible_text(report)
    assert list_items(report) == ["+ https://example.gov.au/new", "- https://example.gov.au/old"]
    assert "Watched Pages Changed" not in visible_text(report)


def test_report_lists_only_the_first_internal_links_when_there_are_many(mocker: MockerFixture) -> None:
    """Tests a scan that finds more new pages than an email lists only lists the first of them, with how many more
    there are, so the email stays small enough to arrive whole (the dashboard lists them all)."""
    mocker.patch.object(config, "email_max_listed_links", 2)
    changes = [
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(f"https://example.gov.au/new-{number}"))
        for number in range(1, 6)
    ]

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=changes))

    assert "New Internal Links (5)" in visible_text(report)
    assert list_items(report) == ["+ https://example.gov.au/new-1", "+ https://example.gov.au/new-2"]
    assert "…and 3 more, all listed on the dashboard's Updates tab." in visible_text(report)


def test_report_highlights_only_the_words_that_changed() -> None:
    edit = make_change(
        make_block("The application fee is $50 per year."), make_block("The application fee is $60 per year.")
    )
    changed = ChangeCreate(
        kind=ChangeKind.TEXT_CHANGED,
        page_url=HttpUrl(PAGE_URL),
        old_block=edit.old_block,
        new_block=edit.new_block,
        similarity=edit.similarity,
    )

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[changed]))
    highlighted: list[str] = [span.get_text() for span in BeautifulSoup(report, "html.parser").select("td span")]

    assert highlighted == ["$50", "$60"]


def test_report_lists_text_that_moved_to_another_section() -> None:
    moved = ChangeCreate(
        kind=ChangeKind.TEXT_CHANGED,
        page_url=HttpUrl(PAGE_URL),
        old_block=make_block("Closed", heading="Saturday"),
        new_block=make_block("Closed", heading="Sunday"),
        similarity=1.0,
    )

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[moved]))

    assert "Text Moved:" in visible_text(report)
    assert "“Closed” moved from Saturday to Sunday" in list_items(report)
    assert "Text Changed:" not in visible_text(report)


def test_report_lists_unreachable_pages_apart_from_changed_pages() -> None:
    unreachable = ChangeCreate(
        kind=ChangeKind.PAGE_UNREACHABLE, page_url=HttpUrl(PAGE_URL), failure_reason="HTTP 500", failure_count=2
    )

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[unreachable]))

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


@pytest.mark.parametrize("status", [status for status in ScanStatus if status is not ScanStatus.SUCCESS])
def test_report_for_every_problem_status_has_a_title_and_an_alert(status: ScanStatus) -> None:
    """Tests a report can be written for every way a scan can go wrong, and says so even without a message."""
    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(status))
    alert = BeautifulSoup(report, "html.parser").select_one(".alert")

    assert f"{STATUS_STYLES[status].title} for https://example.gov.au/" in visible_text(report)
    assert alert is not None
    assert " ".join(alert.get_text().split()) == f"{STATUS_STYLES[status].title} No further details available."


def test_report_shows_edited_text_side_by_side_with_removed_words_red_and_added_words_green() -> None:
    """Tests edited text is a Before/After table, with the words taken out marked red on the left, the words put in
    marked green on the right, and a changed section heading shown as old -> new."""
    changed = ChangeCreate(
        kind=ChangeKind.TEXT_CHANGED,
        page_url=HttpUrl(PAGE_URL),
        old_block=make_block("The fee is $50.", heading="Fees"),
        new_block=make_block("The fee is $60.", heading="Costs"),
        similarity=0.9,
    )

    table = BeautifulSoup(
        generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=[changed])), "html.parser"
    ).select_one("table")
    assert table is not None
    before_cell, after_cell = table.select("td.diff-cell")
    [removed_word] = before_cell.select("span")
    [added_word] = after_cell.select("span")

    assert [head.get_text() for head in table.select("th")] == ["Before", "After"]
    assert "[Fees → Costs]" in table.get_text()
    assert (before_cell.get_text(), removed_word.get_text()) == ("- The fee is $50.", "$50.")
    assert (after_cell.get_text(), added_word.get_text()) == ("+ The fee is $60.", "$60.")
    assert "background-color: #ffc1c0" in str(removed_word.get("style"))
    assert "background-color: #abf2bc" in str(added_word.get("style"))


def test_report_lists_each_kind_of_page_change_under_its_own_label() -> None:
    """Tests links, documents and text taken off a page are listed with a "-", and ones put on with a "+", each under
    a label saying what they are."""
    page_url = HttpUrl(PAGE_URL)
    changes = [
        ChangeCreate(kind=ChangeKind.LINK_REMOVED, page_url=page_url, url=HttpUrl("https://example.gov.au/old")),
        ChangeCreate(kind=ChangeKind.DOCUMENT_ADDED, page_url=page_url, url=HttpUrl("https://example.gov.au/a.pdf")),
        ChangeCreate(kind=ChangeKind.DOCUMENT_REMOVED, page_url=page_url, url=HttpUrl("https://example.gov.au/b.pdf")),
        ChangeCreate(kind=ChangeKind.TEXT_REMOVED, page_url=page_url, old_block=make_block("Closed on Mondays.")),
    ]

    report: str = generate_scan_report_html(WEBSITE_URL, make_scan_run(changes=changes))
    labels: list[str] = [label.get_text() for label in BeautifulSoup(report, "html.parser").select(".change-label")]

    assert labels == ["Links Removed:", "Documents Added:", "Documents Removed:", "Text Removed:"]
    assert list_items(report) == [
        "- https://example.gov.au/old",
        "+ https://example.gov.au/a.pdf",
        "- https://example.gov.au/b.pdf",
        "[Fees] Closed on Mondays.",
    ]


def test_monitoring_started_html_describes_part_day_scans_and_daily_health_checks() -> None:
    """Tests a website scanned twice a day says "every 0.5 days", and a daily health check says "every day"."""
    website = WebsiteCreate(url=HttpUrl("https://example.com"), days_between_scans=0.5)

    body: str = monitoring_started_html(website, 1)

    assert "It is checked every 0.5 days," in visible_text(body)
    assert "short confirmation email every day so" in visible_text(body)
