"""Tests of a scan from a website's history: whether it has a report to email, and what it found on each page."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl

from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.scan_run_models import ChangeKind, ChangeRead, ScanRunRead, ScanStatus

FEES_PAGE: HttpUrl = HttpUrl("https://example.com/fees")
DATES_PAGE: HttpUrl = HttpUrl("https://example.com/dates")
SCANNED_AT: datetime = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def _scan_run(
    changes: list[ChangeRead] | None = None,
    status: ScanStatus = ScanStatus.SUCCESS,
    notified_at: datetime | None = None,
) -> ScanRunRead:
    """Creates a scan that found the given changes."""
    return ScanRunRead(
        id=uuid.uuid4(),
        website_id=uuid.uuid4(),
        scanned_at=SCANNED_AT,
        status=status,
        notified_at=notified_at,
        changes=changes or [],
    )


def _block(text: str) -> ContentBlock:
    """Creates a paragraph of page text."""
    return ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text=text)


def _link_change(kind: ChangeKind, url: str, page_url: HttpUrl | None = None) -> ChangeRead:
    """Creates a change for a link, document or internal page that was added or removed."""
    return ChangeRead(kind=kind, page_url=page_url, url=HttpUrl(url))


@pytest.mark.parametrize(
    ("scan_run", "has_report"),
    [
        (_scan_run(), False),
        (_scan_run(status=ScanStatus.CONNECTION_ERROR), True),
        (_scan_run([_link_change(ChangeKind.INTERNAL_LINK_ADDED, "https://example.com/new")]), True),
    ],
    ids=["nothing-found", "problem", "change-found"],
)
def test_a_scan_has_a_report_when_it_found_a_change_or_a_problem(scan_run: ScanRunRead, has_report: bool) -> None:
    """Tests a scan only has something to tell recipients when it found a change or ran into a problem, and only
    such a scan waits to be emailed."""
    assert scan_run.has_report is has_report
    assert scan_run.is_awaiting_email is has_report


def test_a_scan_whose_report_was_emailed_is_not_awaiting_email() -> None:
    """Tests a report that has been emailed is no longer waiting to be emailed."""
    scan_run = _scan_run(status=ScanStatus.SCAN_ERROR, notified_at=SCANNED_AT)

    assert scan_run.has_report
    assert not scan_run.is_awaiting_email


def test_internal_links_added_and_removed_are_kept_apart() -> None:
    """Tests the website's new and missing pages are listed separately, in the order found, without the links found
    on critical pages."""
    scan_run = _scan_run(
        [
            _link_change(ChangeKind.INTERNAL_LINK_ADDED, "https://example.com/a"),
            _link_change(ChangeKind.INTERNAL_LINK_REMOVED, "https://example.com/old"),
            _link_change(ChangeKind.LINK_ADDED, "https://example.com/apply", page_url=FEES_PAGE),
            _link_change(ChangeKind.INTERNAL_LINK_ADDED, "https://example.com/b"),
        ]
    )

    assert scan_run.internal_links_added == [HttpUrl("https://example.com/a"), HttpUrl("https://example.com/b")]
    assert scan_run.internal_links_removed == [HttpUrl("https://example.com/old")]


def test_changes_are_grouped_per_critical_page_in_the_order_the_pages_were_checked() -> None:
    """Tests each critical page's changes are gathered together and sorted by kind, with the pages in the order
    they were first found, and the website's internal links left out."""
    edited = ChangeRead(
        kind=ChangeKind.TEXT_CHANGED,
        page_url=FEES_PAGE,
        old_block=_block("Fee is $50."),
        new_block=_block("Fee is $60."),
        similarity=0.85,
    )
    scan_run = _scan_run(
        [
            _link_change(ChangeKind.INTERNAL_LINK_ADDED, "https://example.com/new"),
            _link_change(ChangeKind.LINK_REMOVED, "https://example.com/old-form", page_url=FEES_PAGE),
            ChangeRead(kind=ChangeKind.TEXT_ADDED, page_url=DATES_PAGE, new_block=_block("Exams start 3 May.")),
            edited,
            ChangeRead(kind=ChangeKind.TEXT_REMOVED, page_url=FEES_PAGE, old_block=_block("Pay by cheque.")),
            _link_change(ChangeKind.DOCUMENT_ADDED, "https://example.com/dates.pdf", page_url=DATES_PAGE),
        ]
    )

    fees_page, dates_page = scan_run.pages

    assert fees_page.url == FEES_PAGE
    assert fees_page.links_removed == [HttpUrl("https://example.com/old-form")]
    assert fees_page.text_removed == [_block("Pay by cheque.")]
    assert [(change.old_block.text, change.new_block.text) for change in fees_page.text_changed] == [
        ("Fee is $50.", "Fee is $60.")
    ]
    assert fees_page.text_changed[0].similarity == 0.85
    assert fees_page.text_added == [] and fees_page.documents_added == []
    assert dates_page.url == DATES_PAGE
    assert dates_page.text_added == [_block("Exams start 3 May.")]
    assert dates_page.documents_added == [HttpUrl("https://example.com/dates.pdf")]
    assert dates_page.links_removed == [] and dates_page.text_changed == []
    assert not fees_page.is_unreachable and not dates_page.is_unreachable


def test_an_unreachable_critical_page_keeps_why_and_for_how_long() -> None:
    """Tests a critical page that could not be checked is marked unreachable, with the reason and how many checks
    in a row have failed."""
    scan_run = _scan_run(
        [ChangeRead(kind=ChangeKind.PAGE_UNREACHABLE, page_url=FEES_PAGE, failure_reason="HTTP 500", failure_count=3)]
    )

    [fees_page] = scan_run.pages

    assert fees_page.is_unreachable
    assert (fees_page.failure_reason, fees_page.failure_count) == ("HTTP 500", 3)


def test_a_scan_that_found_nothing_on_its_critical_pages_has_no_pages() -> None:
    """Tests a scan that only found new internal pages has no critical page changes to show."""
    scan_run = _scan_run([_link_change(ChangeKind.INTERNAL_LINK_ADDED, "https://example.com/new")])

    assert scan_run.pages == []
