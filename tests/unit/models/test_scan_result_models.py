"""Tests of the scan result models: what a scan of a critical page reports, and what of it is saved."""

import uuid

import pytest
from pydantic import HttpUrl, ValidationError

from app.core.config import config
from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.scan_result_models import CriticalPageScanResult, WebsiteScanResult

PAGE_URL: HttpUrl = HttpUrl("https://example.com/fees")


def test_a_page_result_with_only_a_new_baseline_has_no_changes() -> None:
    """Tests saving a page's copy, without anything new found on it, is not reported as a change."""
    assert not CriticalPageScanResult(
        url=PAGE_URL, text_body="<p>Fees</p>", links=[HttpUrl("https://example.com/a")]
    ).has_changes


def test_a_page_result_with_new_text_has_changes() -> None:
    """Tests text found on the page that was not there before is reported as a change."""
    new_text = ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text="Late fee is $20.")

    assert CriticalPageScanResult(url=PAGE_URL, recent_text_added=[new_text]).has_changes


@pytest.mark.parametrize(
    ("consecutive_failures", "reported"),
    [
        (config.critical_page_alert_after_failures - 1, False),
        (config.critical_page_alert_after_failures, True),
        (config.critical_page_alert_after_failures + 1, False),
    ],
)
def test_an_unreachable_page_is_reported_once_when_it_reaches_the_failure_limit(
    consecutive_failures: int, reported: bool
) -> None:
    """Tests a page that keeps failing is reported on the scan where it reaches the failure limit, and not on the
    scans before or after."""
    result = CriticalPageScanResult(
        url=PAGE_URL, consecutive_failures=consecutive_failures, last_failure_reason="HTTP 500"
    )

    assert result.has_just_reached_failure_limit is reported
    assert result.has_changes is reported


def test_a_bad_link_set_after_the_result_is_made_is_refused() -> None:
    """Tests a value set on a result after it is made is checked too, so a bad link cannot be saved."""
    result = CriticalPageScanResult(url=PAGE_URL)

    with pytest.raises(ValidationError):
        result.links = ["not a url"]  # pyright: ignore[reportAttributeAccessIssue]


def test_only_the_page_as_it_is_now_is_saved_from_a_page_result() -> None:
    """Tests what changed on a page is left out of what is saved over the page's saved copy, as it goes in the scan's
    history instead, and values the scan did not set are left as they are."""
    new_text = ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text="Late fee is $20.")
    result = CriticalPageScanResult(url=PAGE_URL, text_body="<p>Late fee is $20.</p>", recent_text_added=[new_text])

    assert result.as_critical_page_update().model_dump(exclude_unset=True) == {
        "url": PAGE_URL,
        "text_body": "<p>Late fee is $20.</p>",
    }


def test_a_link_that_is_not_a_url_cannot_be_set_on_a_critical_page_scan_result() -> None:
    """Tests a value set on an update after it is made is checked, so a bad link is refused when it is found rather
    than saved and then failing every later scan of the website when it is read back."""
    update = CriticalPageScanResult(url=HttpUrl("https://example.com/"))

    with pytest.raises(ValidationError):
        update.recent_links_added = ["sms:+61400000000"]  # pyright: ignore[reportAttributeAccessIssue]


# ======================================
# WebsiteScanResult change detection
# ======================================

CHANGED_PAGE_ID = uuid.uuid4()
BASELINE_PAGE_ID = uuid.uuid4()


@pytest.mark.parametrize(
    ("updates", "changed_page_ids", "has_changes"),
    [
        (WebsiteScanResult(), set[uuid.UUID](), False),
        (WebsiteScanResult(initial_internal_links=[HttpUrl("https://www.test_website.com/")]), set[uuid.UUID](), False),
        (WebsiteScanResult(recent_added_internal_links=[], recent_removed_internal_links=[]), set[uuid.UUID](), False),
        (
            WebsiteScanResult(recent_removed_internal_links=[HttpUrl("https://www.test_website.com/old")]),
            set[uuid.UUID](),
            True,
        ),
        (
            WebsiteScanResult(
                critical_page_updates={
                    CHANGED_PAGE_ID: CriticalPageScanResult(
                        url=HttpUrl("https://www.test_website.com/fees"),
                        recent_links_added=[HttpUrl("https://www.test_website.com/new")],
                    ),
                    BASELINE_PAGE_ID: CriticalPageScanResult(
                        url=HttpUrl("https://www.test_website.com/new-page"),
                        text_body="<p>Hi</p>",
                        links=[],
                        documents=[],
                    ),
                }
            ),
            {CHANGED_PAGE_ID},
            True,
        ),
    ],
    ids=["empty", "internal-link-baseline", "empty-link-diff", "link-removed", "changed-and-baseline-pages"],
)
def test_website_scan_result_change_detection(
    updates: WebsiteScanResult, changed_page_ids: set[uuid.UUID], has_changes: bool
):
    """Tests only real changes count as changes, and pages that only saved a baseline are not reported."""
    assert updates.changed_page_ids == changed_page_ids
    assert updates.has_changes is has_changes
