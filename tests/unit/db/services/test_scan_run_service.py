import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import IntegrityError
from app.db.schema import DBChange, DBScanRun
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunCreate, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteCreate, WebsiteRead

FIRST_SCAN_AT: datetime = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
PAGE_URL: HttpUrl = HttpUrl("https://www.test_website.com/fees")


def _block(text: str) -> ContentBlock:
    """Builds a paragraph under a "Fees" heading."""
    return ContentBlock(parent_heading="Fees", block_type=HTMLBlockType.PARAGRAPH, text=text)


def _link_added(url: str) -> ChangeCreate:
    """Builds a change for a link added to the fees page."""
    return ChangeCreate(kind=ChangeKind.LINK_ADDED, page_url=PAGE_URL, url=HttpUrl(url))


def _add_scan(
    session: Session,
    website: WebsiteRead,
    days_after_first: int = 0,
    status: ScanStatus = ScanStatus.SUCCESS,
    changes: list[ChangeCreate] | None = None,
) -> ScanRunRead:
    """Adds a scan of the website to its history, a number of days after the first scan."""
    return ScanRunService(session).create(
        ScanRunCreate(
            website_id=website.id,
            scanned_at=FIRST_SCAN_AT + timedelta(days=days_after_first),
            status=status,
            changes=changes or [],
        )
    )


def test_create_saves_every_kind_of_change_in_the_order_it_was_found(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests a scan's changes are read back as they were saved, including the text blocks inside them."""
    changes = [
        ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl("https://www.test_website.com/new-page")),
        _link_added("https://www.test_website.com/apply"),
        ChangeCreate(kind=ChangeKind.TEXT_ADDED, page_url=PAGE_URL, new_block=_block("Late fee is $20.")),
        ChangeCreate(
            kind=ChangeKind.TEXT_CHANGED,
            page_url=PAGE_URL,
            old_block=_block("Fee is $50."),
            new_block=_block("Fee is $60."),
            similarity=0.85,
        ),
        ChangeCreate(
            kind=ChangeKind.PAGE_UNREACHABLE,
            page_url=HttpUrl("https://www.test_website.com/dates"),
            failure_reason="HTTP 500",
            failure_count=2,
        ),
    ]

    created: ScanRunRead = _add_scan(session, test_website, changes=changes)
    session.expire_all()
    [saved] = ScanRunService(session).get_latest_for_website(test_website.id, limit=1)

    assert saved.id == created.id
    assert [change.kind for change in saved.changes] == [change.kind for change in changes]
    assert saved.internal_links_added == [HttpUrl("https://www.test_website.com/new-page")]
    fees_page, dates_page = saved.pages
    assert fees_page.links_added == [HttpUrl("https://www.test_website.com/apply")]
    assert fees_page.text_added == [_block("Late fee is $20.")]
    assert fees_page.text_changed[0].old_block.text == "Fee is $50."
    assert fees_page.text_changed[0].new_block.text == "Fee is $60."
    assert fees_page.text_changed[0].similarity == 0.85
    assert dates_page.is_unreachable
    assert (dates_page.failure_reason, dates_page.failure_count) == ("HTTP 500", 2)


def test_get_latest_for_website_returns_its_newest_scans_first(session: Session, test_website: WebsiteRead) -> None:
    """Tests a website's history is read newest first, only up to the number asked for."""
    for day in range(3):
        _add_scan(session, test_website, days_after_first=day)

    latest: list[ScanRunRead] = ScanRunService(session).get_latest_for_website(test_website.id, limit=2)

    assert [scan_run.scanned_at for scan_run in latest] == [
        FIRST_SCAN_AT + timedelta(days=2),
        FIRST_SCAN_AT + timedelta(days=1),
    ]


def test_get_awaiting_email_only_returns_unsent_scans_with_something_to_report(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests a scan that found nothing, or whose report was already emailed, is not waiting for an email, while
    unsent changes and unsent failure reports are, oldest first."""
    _add_scan(session, test_website, days_after_first=0)
    failed = _add_scan(session, test_website, days_after_first=1, status=ScanStatus.CONNECTION_ERROR)
    changed = _add_scan(session, test_website, days_after_first=2, changes=[_link_added("https://a.com/")])
    already_sent = _add_scan(session, test_website, days_after_first=3, changes=[_link_added("https://b.com/")])
    ScanRunService(session).mark_emailed([already_sent.id], emailed_at=datetime(2026, 10, 4, 9, 5, tzinfo=UTC))

    awaiting: list[ScanRunRead] = ScanRunService(session).get_awaiting_email()

    assert [scan_run.id for scan_run in awaiting] == [failed.id, changed.id]


def test_mark_emailed_records_when_each_report_was_sent(session: Session, test_website: WebsiteRead) -> None:
    """Tests the scans marked as emailed are given the time they were emailed, and other scans are left alone."""
    sent = _add_scan(session, test_website, days_after_first=0, changes=[_link_added("https://a.com/")])
    unsent = _add_scan(session, test_website, days_after_first=1, changes=[_link_added("https://b.com/")])
    emailed_at = datetime(2026, 10, 2, 9, 5, tzinfo=UTC)

    ScanRunService(session).mark_emailed([sent.id], emailed_at=emailed_at)
    by_id = {scan_run.id: scan_run for scan_run in ScanRunService(session).get_latest_for_website(test_website.id, 2)}

    assert by_id[sent.id].notified_at == emailed_at
    assert by_id[unsent.id].notified_at is None
    assert by_id[unsent.id].is_awaiting_email


def test_delete_older_scans_keeps_the_latest_and_any_report_not_yet_emailed(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests a website's history is trimmed to its most recent scans, except that a report whose email failed is kept
    (with its changes) until it has been sent."""
    unsent = _add_scan(session, test_website, days_after_first=0, changes=[_link_added("https://a.com/")])
    sent = _add_scan(session, test_website, days_after_first=1, changes=[_link_added("https://b.com/")])
    ScanRunService(session).mark_emailed([sent.id], emailed_at=datetime(2026, 10, 2, 9, 5, tzinfo=UTC))
    latest = [
        _add_scan(session, test_website, days_after_first=day, changes=[_link_added(f"https://c{day}.com/")])
        for day in range(2, 4)
    ]

    ScanRunService(session).delete_older_scans(test_website.id, keep=2)
    kept_ids = {scan_run.id for scan_run in ScanRunService(session).get_latest_for_website(test_website.id, 10)}

    assert kept_ids == {unsent.id, *(scan_run.id for scan_run in latest)}
    assert sent.id not in kept_ids
    assert session.scalar(select(func.count()).select_from(DBChange)) == 1  # The unsent report's change


def test_deleting_a_website_deletes_its_history(session: Session, test_website: WebsiteRead) -> None:
    """Tests a deleted website's scans and their changes are deleted with it."""
    _add_scan(session, test_website, changes=[_link_added("https://a.com/")])

    WebsiteService(session).delete(test_website.id)

    assert session.scalar(select(func.count()).select_from(DBScanRun)) == 0
    assert session.scalar(select(func.count()).select_from(DBChange)) == 0


def test_a_scan_of_a_website_that_does_not_exist_is_refused(session: Session) -> None:
    """Tests a scan cannot be saved for a website that has been deleted, so no report is left with nobody to send it
    to."""
    with pytest.raises(IntegrityError):
        ScanRunService(session).create(
            ScanRunCreate(website_id=uuid.uuid4(), scanned_at=FIRST_SCAN_AT, status=ScanStatus.SCAN_ERROR)
        )


@pytest.fixture
def other_website(session: Session) -> WebsiteRead:
    """Provides a second website, whose scans must not be touched by work on the test website's scans."""
    return WebsiteService(session).create(WebsiteCreate(url=HttpUrl("https://other.example.com")))


def test_delete_older_scans_only_trims_the_given_website(
    session: Session, test_website: WebsiteRead, other_website: WebsiteRead
) -> None:
    """Tests trimming one website's history leaves every scan of another website alone."""
    for day in range(3):
        _add_scan(session, test_website, days_after_first=day)
        _add_scan(session, other_website, days_after_first=day)

    ScanRunService(session).delete_older_scans(test_website.id, keep=1)

    assert len(ScanRunService(session).get_latest_for_website(test_website.id, limit=10)) == 1
    assert len(ScanRunService(session).get_latest_for_website(other_website.id, limit=10)) == 3


def test_delete_older_scans_keeps_an_unsent_failure_report_but_not_a_sent_one(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests an old failed scan whose report has not been emailed is kept, while one already emailed is deleted."""
    unsent_failure = _add_scan(session, test_website, days_after_first=0, status=ScanStatus.CONNECTION_ERROR)
    sent_failure = _add_scan(session, test_website, days_after_first=1, status=ScanStatus.CONNECTION_ERROR)
    ScanRunService(session).mark_emailed([sent_failure.id], emailed_at=datetime(2026, 10, 2, 9, 5, tzinfo=UTC))
    latest = _add_scan(session, test_website, days_after_first=2, changes=[_link_added("https://a.com/")])

    ScanRunService(session).delete_older_scans(test_website.id, keep=1)

    kept_ids = {scan_run.id for scan_run in ScanRunService(session).get_latest_for_website(test_website.id, 10)}
    assert kept_ids == {unsent_failure.id, latest.id}


def test_delete_older_scans_keeps_only_the_newest_scan_that_found_nothing(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests a run of empty scans leaves just the newest one, and does not push out scans that found changes."""
    with_changes = [
        _add_scan(session, test_website, days_after_first=day, changes=[_link_added(f"https://c{day}.com/")])
        for day in range(2)
    ]
    for day in range(2, 5):
        _add_scan(session, test_website, days_after_first=day)
    newest_empty = _add_scan(session, test_website, days_after_first=5)
    for scan_run in with_changes:
        ScanRunService(session).mark_emailed([scan_run.id], emailed_at=FIRST_SCAN_AT + timedelta(days=6))

    ScanRunService(session).delete_older_scans(test_website.id, keep=2)
    kept_ids = {scan_run.id for scan_run in ScanRunService(session).get_latest_for_website(test_website.id, 10)}

    assert kept_ids == {newest_empty.id, *(scan_run.id for scan_run in with_changes)}


def test_delete_older_scans_removes_the_empty_scan_once_a_newer_scan_finds_changes(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests the one kept empty scan is deleted when the next scan finds changes."""
    empty = _add_scan(session, test_website, days_after_first=0)
    with_changes = _add_scan(session, test_website, days_after_first=1, changes=[_link_added("https://a.com/")])

    ScanRunService(session).delete_older_scans(test_website.id, keep=7)
    kept_ids = {scan_run.id for scan_run in ScanRunService(session).get_latest_for_website(test_website.id, 10)}

    assert kept_ids == {with_changes.id}
    assert empty.id not in kept_ids


def test_get_awaiting_email_returns_reports_of_every_website_oldest_first(
    session: Session, test_website: WebsiteRead, other_website: WebsiteRead
) -> None:
    """Tests waiting reports are gathered from every website, in the order the scans ran."""
    newest = _add_scan(session, test_website, days_after_first=2, status=ScanStatus.SCAN_ERROR)
    oldest = _add_scan(session, other_website, days_after_first=0, status=ScanStatus.SCAN_ERROR)
    middle = _add_scan(session, test_website, days_after_first=1, changes=[_link_added("https://a.com/")])

    awaiting: list[ScanRunRead] = ScanRunService(session).get_awaiting_email()

    assert [scan_run.id for scan_run in awaiting] == [oldest.id, middle.id, newest.id]
