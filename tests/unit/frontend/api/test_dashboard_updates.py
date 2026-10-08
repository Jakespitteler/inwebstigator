"""Tests of the dashboard's Updates tab: each website's recent scans and what each scan found."""

from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup, Tag
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.db.services.scan_run_service import ScanRunService
from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunCreate, ScanStatus
from app.models.website_models import WebsiteRead

FEES_PAGE: str = "https://www.test_website.com/fees"
DATES_PAGE: str = "https://www.test_website.com/dates"


def _add_scan(
    session: Session,
    website: WebsiteRead,
    changes: list[ChangeCreate] | None = None,
    status: ScanStatus = ScanStatus.SUCCESS,
    scanned_at: datetime | None = None,
) -> None:
    """Adds a scan of the website to its history, which found the given changes."""
    ScanRunService(session).create(
        ScanRunCreate(
            website_id=website.id,
            scanned_at=scanned_at or datetime.now(UTC),
            status=status,
            changes=changes or [],
        )
    )


def _paragraph(text: str, section: str | None = None) -> ContentBlock:
    """Creates a paragraph of page text under the given section heading."""
    return ContentBlock(parent_heading=section, block_type=HTMLBlockType.PARAGRAPH, text=text)


def _text_changed(old: ContentBlock, new: ContentBlock, page_url: str = FEES_PAGE) -> ChangeCreate:
    """Creates a change for a block of text on a critical page that was edited or moved."""
    return ChangeCreate(
        kind=ChangeKind.TEXT_CHANGED, page_url=HttpUrl(page_url), old_block=old, new_block=new, similarity=0.8
    )


def _updates_panel(api_client: TestClient) -> Tag:
    """Loads the dashboard and returns its Updates tab."""
    response = api_client.get("/")
    assert response.status_code == 200, response.text
    panel = BeautifulSoup(response.text, "html.parser").select_one("#updates-panel")
    assert panel is not None
    return panel


def _text(tag: Tag) -> str:
    """Returns a tag's text with its whitespace collapsed, as a user reads it."""
    return " ".join(tag.get_text().split())


def _comparison(change_section: Tag, heading: str) -> tuple[Tag, Tag]:
    """Returns the Before and After cells of the comparison table under a heading, e.g. "Content changed"."""
    title = change_section.find("h2", string=heading)
    assert isinstance(title, Tag), f"No {heading!r} heading"
    table = title.find_next_sibling("table")
    assert isinstance(table, Tag)
    assert [_text(cell) for cell in table.select("th")] == ["Before", "After"]
    before, after = table.select("tbody td")
    return before, after


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (ScanStatus.TRAFFIC_ERROR, "Rate limited"),
        (ScanStatus.CONNECTION_ERROR, "Could not connect"),
        (ScanStatus.SCAN_ERROR, "Scan failed"),
        (ScanStatus.SKIPPED_DEACTIVATED, "Skipped, website switched off"),
        (ScanStatus.SKIPPED_COOLDOWN, "Skipped, on cooldown"),
        (ScanStatus.TOO_LARGE, "Too large to crawl"),
        (ScanStatus.PAGES_MISSING, "Most pages missing"),
    ],
)
def test_a_scan_that_did_not_go_normally_says_how_it_went(
    api_client: TestClient, session: Session, test_website: WebsiteRead, status: ScanStatus, label: str
) -> None:
    """Tests a scan that ran into a problem is marked with a few words saying what went wrong, and its report is
    shown as not emailed yet."""
    _add_scan(session, test_website, status=status)

    scan = _updates_panel(api_client).select_one(".scan-change-record")

    assert scan is not None
    problem = scan.select_one(".scan-problem")
    assert problem is not None and _text(problem) == label
    assert "Email not sent yet" in _text(scan)


def test_a_scan_that_found_nothing_says_no_changes_and_has_no_email_to_send(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests a normal scan that found nothing says "No changes", with no problem label and no email waiting, as it
    has no report to email."""
    _add_scan(session, test_website)

    panel = _updates_panel(api_client)
    scan = panel.select_one(".scan-change-record")

    assert scan is not None
    assert scan.select(".scan-problem") == []
    assert "No changes" in _text(scan)
    assert "Email not sent yet" not in _text(scan)
    assert "No changes in its only scan" in _text(panel)


def test_websites_that_have_not_been_scanned_are_left_out(api_client: TestClient, test_website: WebsiteRead) -> None:
    """Tests a website with no scans yet has no card on the Updates tab, which says nothing has been scanned yet."""
    panel = _updates_panel(api_client)

    assert panel.select(".website-change-record") == []
    assert "No websites have been scanned yet." in _text(panel)


def test_a_website_card_counts_the_scans_that_found_something(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests a website's card says how many of its recent scans found changes."""
    _add_scan(session, test_website, scanned_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    _add_scan(
        session,
        test_website,
        changes=[ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(f"{test_website.url}new"))],
        scanned_at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
    )

    website_card = _updates_panel(api_client).select_one(".website-change-record")

    assert website_card is not None
    assert "1 of 2 scans found changes" in _text(website_card)


def test_internal_links_added_and_removed_are_listed(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests the pages found on or gone from the website are listed as added or removed, and counted."""
    _add_scan(
        session,
        test_website,
        changes=[
            ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(f"{test_website.url}new-page")),
            ChangeCreate(kind=ChangeKind.INTERNAL_LINK_REMOVED, url=HttpUrl(f"{test_website.url}old-page")),
        ],
    )

    scan = _updates_panel(api_client).select_one(".scan-change-record")

    assert scan is not None
    assert "2 internal links" in _text(scan)
    assert [_text(item) for item in scan.select(".change-item-added")] == [f"{test_website.url}new-page Added"]
    assert [_text(item) for item in scan.select(".change-item-removed")] == [f"{test_website.url}old-page Removed"]


def test_critical_page_changes_are_grouped_per_page(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests each critical page's changes are shown together in the page's own card, in the order the pages were
    checked, even when the scan found them mixed together."""
    _add_scan(
        session,
        test_website,
        changes=[
            ChangeCreate(kind=ChangeKind.LINK_ADDED, page_url=HttpUrl(FEES_PAGE), url=HttpUrl(f"{FEES_PAGE}/2027")),
            ChangeCreate(
                kind=ChangeKind.TEXT_ADDED, page_url=HttpUrl(DATES_PAGE), new_block=_paragraph("Exams start 3 May")
            ),
            ChangeCreate(
                kind=ChangeKind.DOCUMENT_REMOVED, page_url=HttpUrl(FEES_PAGE), url=HttpUrl(f"{FEES_PAGE}/fees.pdf")
            ),
        ],
    )

    scan = _updates_panel(api_client).select_one(".scan-change-record")

    assert scan is not None
    assert "2 critical pages changed" in _text(scan)
    fees_card, dates_card = scan.select(".page-change-record")
    assert _text(fees_card).startswith(FEES_PAGE)
    assert "1 link" in _text(fees_card) and "1 document" in _text(fees_card)
    assert f"{FEES_PAGE}/2027" in _text(fees_card) and "fees.pdf Removed" in _text(fees_card)
    assert "Exams start 3 May" not in _text(fees_card)
    assert _text(dates_card).startswith(DATES_PAGE)
    assert "1 text change" in _text(dates_card)
    assert "Exams start 3 May Added" in _text(dates_card)


def test_edited_text_shows_before_and_after_with_the_changed_words_marked(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests an edited block of text is shown before and after the edit, with only the removed and added words
    highlighted, so the user can see what changed in a long paragraph."""
    _add_scan(
        session,
        test_website,
        changes=[
            _text_changed(_paragraph("The fee is $50 per year", "Fees"), _paragraph("The fee is $60 per year", "Fees"))
        ],
    )

    page_card = _updates_panel(api_client).select_one(".page-change-record")

    assert page_card is not None
    assert page_card.find("h2", string="Section changed") is None  # The block stayed in the same section
    before, after = _comparison(page_card, "Content changed")
    assert _text(before) == "The fee is $50 per year"
    assert _text(after) == "The fee is $60 per year"
    assert [_text(word) for word in before.select(".removed-word")] == ["$50"]
    assert [_text(word) for word in after.select(".added-word")] == ["$60"]
    assert before.select(".added-word") == [] and after.select(".removed-word") == []


def test_a_block_moved_to_another_section_shows_the_section_change(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests a block whose text stayed the same but moved under another heading is shown as a section change,
    without a content change."""
    _add_scan(
        session,
        test_website,
        changes=[_text_changed(_paragraph("Closed", "Saturday"), _paragraph("Closed", "Sunday"))],
    )

    page_card = _updates_panel(api_client).select_one(".page-change-record")

    assert page_card is not None
    before, after = _comparison(page_card, "Section changed")
    assert (_text(before), _text(after)) == ("Saturday", "Sunday")
    assert page_card.find("h2", string="Content changed") is None


def test_edited_text_from_a_page_is_shown_as_text_not_run_as_html(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests text read from a watched website that looks like HTML is shown as text, so a page cannot add its own
    elements (e.g. a script) to the dashboard."""
    _add_scan(
        session,
        test_website,
        changes=[_text_changed(_paragraph("Fees <b>soon</b>"), _paragraph("Fees <script>alert(1)</script>"))],
    )

    page_card = _updates_panel(api_client).select_one(".page-change-record")

    assert page_card is not None
    assert page_card.select("script, b") == []
    before, after = _comparison(page_card, "Content changed")
    assert _text(before) == "Fees <b>soon</b>"
    assert _text(after) == "Fees <script>alert(1)</script>"


def test_an_unreachable_critical_page_says_why_and_for_how_long(
    api_client: TestClient, session: Session, test_website: WebsiteRead
) -> None:
    """Tests a critical page that could not be reached is counted as unreachable, not changed, and says why and
    for how many scans in a row."""
    _add_scan(
        session,
        test_website,
        changes=[
            ChangeCreate(
                kind=ChangeKind.PAGE_UNREACHABLE,
                page_url=HttpUrl(FEES_PAGE),
                failure_reason="404 Not Found",
                failure_count=2,
            )
        ],
    )

    scan = _updates_panel(api_client).select_one(".scan-change-record")

    assert scan is not None
    assert "1 critical page unreachable" in _text(scan)
    assert "critical page changed" not in _text(scan)
    assert "Could not be reached: 404 Not Found (2 scans in a row)." in _text(scan)
