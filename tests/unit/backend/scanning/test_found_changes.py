import uuid

import pytest
from pydantic import HttpUrl

from app.backend.scanning.found_changes import changes_found_by
from app.core.config import config
from app.models.content_block_models import ChangedBlock, ContentBlock, HTMLBlockType
from app.models.scan_result_models import CriticalPageScanResult, WebsiteScanResult
from app.models.scan_run_models import ChangeCreate, ChangeKind

ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures
WEBSITE_URL: str = "https://www.test_website.com"
FEES_PAGE: HttpUrl = HttpUrl(f"{WEBSITE_URL}/fees")
DATES_PAGE: HttpUrl = HttpUrl(f"{WEBSITE_URL}/dates")


def _url(path: str) -> HttpUrl:
    """Makes a URL on the test website."""
    return HttpUrl(f"{WEBSITE_URL}/{path}")


def _paragraph(text: str) -> ContentBlock:
    """Makes a paragraph of text under the "Fees" heading."""
    return ContentBlock(parent_heading="Fees", block_type=HTMLBlockType.PARAGRAPH, text=text)


def _failed_check(consecutive_failures: int) -> CriticalPageScanResult:
    """Makes what a scan finds for a critical page that could not be checked."""
    return CriticalPageScanResult(
        url=FEES_PAGE, consecutive_failures=consecutive_failures, last_failure_reason="HTTP 500"
    )


@pytest.mark.parametrize(
    "website_updates",
    [
        None,
        WebsiteScanResult(
            initial_internal_links=[_url(""), _url("about")],
            critical_page_updates={
                uuid.uuid4(): CriticalPageScanResult(
                    url=FEES_PAGE, text_body="<p>The fee is $100.</p>", links=[_url("apply")], documents=[]
                )
            },
        ),
        WebsiteScanResult(critical_page_updates={uuid.uuid4(): _failed_check(ALERT_AFTER_FAILURES - 1)}),
        WebsiteScanResult(critical_page_updates={uuid.uuid4(): _failed_check(ALERT_AFTER_FAILURES + 1)}),
        WebsiteScanResult(
            critical_page_updates={
                uuid.uuid4(): CriticalPageScanResult(url=FEES_PAGE, consecutive_failures=0, last_failure_reason=None)
            }
        ),
    ],
    ids=["nothing-changed", "baselines", "first-failed-check", "page-still-down", "page-back-after-failing"],
)
def test_a_scan_with_nothing_to_report_has_no_changes(website_updates: WebsiteScanResult | None) -> None:
    """Tests a scan that found nothing, only saved baselines, or only counted or cleared a page's failed checks
    (without the page just reaching the failure limit) adds no changes to the website's history."""
    assert changes_found_by(website_updates) == []


def test_every_change_is_listed_internal_pages_first_then_each_critical_page_in_order() -> None:
    """Tests each added or removed internal page, link and document, and each block of text added, removed or edited,
    becomes one change: internal pages first, then each critical page in the order it was checked, with the page's
    links, documents, then text."""
    added_text, removed_text = _paragraph("Fees are due in June."), _paragraph("Late fees apply.")
    edit = ChangedBlock(
        old_block=_paragraph("The fee is $100."), new_block=_paragraph("The fee is $120."), similarity=0.9
    )
    website_updates = WebsiteScanResult(
        recent_added_internal_links=[_url("new-page")],
        recent_removed_internal_links=[_url("old-page"), _url("older-page")],
        critical_page_updates={
            uuid.uuid4(): CriticalPageScanResult(
                url=FEES_PAGE,
                recent_links_added=[_url("apply")],
                recent_links_removed=[_url("enquire")],
                recent_documents_added=[_url("fees-2027.pdf")],
                recent_documents_removed=[_url("fees-2026.pdf")],
                recent_text_added=[added_text],
                recent_text_removed=[removed_text],
                recent_text_changed=[edit],
            ),
            uuid.uuid4(): CriticalPageScanResult(url=DATES_PAGE, recent_links_added=[_url("timetable")]),
        },
    )

    assert changes_found_by(website_updates) == [
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=_url("new-page")),
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_REMOVED, url=_url("old-page")),
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_REMOVED, url=_url("older-page")),
        ChangeCreate(kind=ChangeKind.LINK_ADDED, page_url=FEES_PAGE, url=_url("apply")),
        ChangeCreate(kind=ChangeKind.LINK_REMOVED, page_url=FEES_PAGE, url=_url("enquire")),
        ChangeCreate(kind=ChangeKind.DOCUMENT_ADDED, page_url=FEES_PAGE, url=_url("fees-2027.pdf")),
        ChangeCreate(kind=ChangeKind.DOCUMENT_REMOVED, page_url=FEES_PAGE, url=_url("fees-2026.pdf")),
        ChangeCreate(kind=ChangeKind.TEXT_ADDED, page_url=FEES_PAGE, new_block=added_text),
        ChangeCreate(kind=ChangeKind.TEXT_REMOVED, page_url=FEES_PAGE, old_block=removed_text),
        ChangeCreate(
            kind=ChangeKind.TEXT_CHANGED,
            page_url=FEES_PAGE,
            old_block=edit.old_block,
            new_block=edit.new_block,
            similarity=0.9,
        ),
        ChangeCreate(kind=ChangeKind.LINK_ADDED, page_url=DATES_PAGE, url=_url("timetable")),
    ]


def test_a_page_that_has_just_reached_the_failure_limit_is_reported_once_as_unreachable() -> None:
    """Tests the scan where a critical page reaches the failure limit records one "unreachable" change for it, saying
    why it failed and how many checks in a row have failed."""
    website_updates = WebsiteScanResult(critical_page_updates={uuid.uuid4(): _failed_check(ALERT_AFTER_FAILURES)})

    assert changes_found_by(website_updates) == [
        ChangeCreate(
            kind=ChangeKind.PAGE_UNREACHABLE,
            page_url=FEES_PAGE,
            failure_reason="HTTP 500",
            failure_count=ALERT_AFTER_FAILURES,
        )
    ]
