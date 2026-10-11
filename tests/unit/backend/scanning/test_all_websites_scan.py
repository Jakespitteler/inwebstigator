import asyncio
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.backend.scanning.all_websites_scan import (
    SCAN_FAILED_MESSAGE,
    run_all_scans,
    scan_all_websites,
    scan_all_websites_now,
)
from app.backend.scanning.scan_queue import ScanQueue
from app.backend.scanning.scan_reports import send_reports_awaiting_email
from app.backend.scanning.website_scan import record_scan
from app.core.errors import NotFoundError, ScanAlreadyQueuedError, ScanCancelledError
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteCreate, WebsiteRead
from tests.fakes import FakeEmailSender
from tests.unit.backend.email_service.builders import make_scan_run


@pytest.fixture
def websites_unchanged_during_run(mocker: MockerFixture) -> None:
    """Makes a scan of all websites use each website as it was listed, as if none changed during the run."""

    def as_listed(website: WebsiteRead) -> WebsiteRead:
        return website

    mocker.patch("app.backend.scanning.all_websites_scan._latest_state", side_effect=as_listed)


@pytest.fixture(autouse=True)
def mock_send_reports(mocker: MockerFixture) -> MagicMock:
    """Stands in for emailing the reports waiting to be sent, which `test_scan_reports.py` tests.

    Returns:
        The mock, to check the reports were sent once the scans finished.
    """
    return mocker.patch("app.backend.scanning.all_websites_scan.send_reports_awaiting_email")


@pytest.fixture(autouse=True)
def mock_record_scan(mocker: MockerFixture) -> MagicMock:
    """Stands in for adding a failed scan to its website's history, so no test writes to the app's real database.

    Returns:
        The mock, which returns the scan as it would be recorded.
    """

    def record(session: object, website: WebsiteRead, status: ScanStatus, message: str | None = None) -> ScanRunRead:
        return make_scan_run(status, message)

    return mocker.patch("app.backend.scanning.all_websites_scan.record_scan", side_effect=record)


def _scan_finding(new_page: str) -> ScanRunRead:
    """Returns a scan that found a new page on the website."""
    return make_scan_run(changes=[ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(new_page))])


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_skips_cooldown_and_recent_scans(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
    mock_send_reports: MagicMock,
    email_sender: FakeEmailSender,
):
    """Tests that websites on active cooldown, or websites scanned too recently are skipped, while reports from
    earlier scans that have not been emailed yet are still sent."""
    cooldown_site = populated_website.model_copy(
        update={
            "id": 2,
            "active": True,
            "url": "https://cooldown.com",
            "on_cooldown_until": datetime.now(UTC) + timedelta(days=1),
        }
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "id": 3,
            "active": True,
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(UTC),
            "days_between_scans": 7,
        }
    )

    mocker.patch.object(
        WebsiteService,
        "get_all",
        return_value=[cooldown_site, recently_scanned_site],
    )
    mock_scan_website = mocker.patch("app.backend.scanning.all_websites_scan.scan_website")
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    mock_scan_website.assert_not_called()
    mock_send_reports.assert_called_once_with(email_sender)
    assert result is None


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_ignoring_the_schedule_scans_websites_not_due_but_skips_cooldown(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests "Run All Scans" also scans websites scanned too recently, while websites on cooldown are still skipped."""
    cooldown_site = populated_website.model_copy(
        update={"url": "https://cooldown.com", "on_cooldown_until": datetime.now(UTC) + timedelta(days=1)}
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(UTC),
            "days_between_scans": 7,
        }
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[cooldown_site, recently_scanned_site])
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website", return_value=make_scan_run()
    )
    mocker.patch.object(WebsiteService, "update")

    await scan_all_websites(ignore_schedule=True)

    mock_scan_website.assert_awaited_once()
    assert mock_scan_website.call_args.args[1].url == recently_scanned_site.url


def _due_website(website: WebsiteRead, url: str, recipient_email: str) -> WebsiteRead:
    """Returns a copy of the website that is due a scan, with a single recipient."""
    recipient = website.recipients[0].model_copy(update={"email": recipient_email})
    return website.model_copy(
        update={
            "id": uuid.uuid4(),
            "url": url,
            "active": True,
            "on_cooldown_until": None,
            "last_scan_at": None,
            "recipients": [recipient],
        }
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_continues_after_a_website_fails(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
    mock_record_scan: MagicMock,
    mock_send_reports: MagicMock,
):
    """Tests one website's unexpected failure does not stop the others being scanned and reported, and its
    failure is recorded, so its recipients are told the scan failed rather than being left to think nothing
    changed."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    broken = _due_website(populated_website, "https://broken.com", "broken@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, broken, last])
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website",
        side_effect=[
            _scan_finding("https://first.com/new"),
            RuntimeError("Unexpected scan failure"),
            _scan_finding("https://last.com/new"),
        ],
    )
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert mock_scan_website.call_count == 3
    [failed_scan] = mock_record_scan.call_args_list
    assert failed_scan.args[1:] == (broken, ScanStatus.SCAN_ERROR, SCAN_FAILED_MESSAGE)
    mock_send_reports.assert_called_once()
    assert result is not None
    assert result.index("https://first.com/new") < result.index("Scan Failure Report") < result.index("last.com/new")

    # The broken website's scan time is still recorded, so it is retried at its normal interval
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [first.id, broken.id, last.id]


@pytest.mark.anyio
async def test_scan_all_websites_continues_after_a_database_error(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
    mock_send_reports: MagicMock,
):
    """Tests a website that cannot be read, or whose scan time cannot be saved, does not end the run, so the
    other websites are still scanned and the reports are still emailed."""
    unreadable = _due_website(populated_website, "https://unreadable.com", "unreadable@gmail.com")
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[unreadable, first, last])

    def get_latest(website: WebsiteRead) -> WebsiteRead:
        if website.id == unreadable.id:
            raise RuntimeError("database is locked")
        return website

    mocker.patch("app.backend.scanning.all_websites_scan._latest_state", side_effect=get_latest)
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website",
        side_effect=[_scan_finding("https://first.com/new"), _scan_finding("https://last.com/new")],
    )
    mocker.patch.object(WebsiteService, "update", side_effect=[RuntimeError("database is locked"), None])

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [first, last]
    mock_send_reports.assert_called_once()
    assert result is not None
    assert "https://first.com/new" in result and "https://last.com/new" in result


@pytest.mark.anyio
@pytest.mark.usefixtures("scan_uses_test_database")
async def test_scan_all_websites_reads_every_website(session: Session, mocker: MockerFixture):
    """Tests every website is scanned in the run, not just the first page of 100."""
    websites = [_add_website(session, f"https://site{number}.example.com", []) for number in range(101)]
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website", return_value=make_scan_run()
    )

    await scan_all_websites()

    scanned_ids = {call.args[1].id for call in mock_scan_website.call_args_list}
    assert scanned_ids == {website.id for website in websites}


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_only_returns_reports_with_something_to_say(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a scan that found nothing adds nothing to the returned reports."""
    quiet = _due_website(populated_website, "https://quiet.com", "quiet@gmail.com")
    changed = _due_website(populated_website, "https://changed.com", "changed@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[quiet, changed])
    mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website",
        side_effect=[make_scan_run(), _scan_finding("https://changed.com/new")],
    )
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert result is not None
    assert "https://quiet.com" not in result
    assert "https://changed.com/new" in result


@pytest.mark.anyio
async def test_scan_all_websites_skips_websites_deleted_during_the_run(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website deleted after the run started is not scanned, as it is checked again just before its turn."""
    deleted = _due_website(populated_website, "https://deleted.com", "deleted@gmail.com")
    kept = _due_website(populated_website, "https://kept.com", "kept@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[deleted, kept])

    def get_latest(id: uuid.UUID) -> WebsiteRead:
        if id == deleted.id:
            raise NotFoundError(id=id)
        return kept

    mocker.patch.object(WebsiteService, "get", side_effect=get_latest)
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website", return_value=_scan_finding("https://kept.com/new")
    )
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [kept]
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [kept.id]
    assert result is not None and "https://kept.com/new" in result


@pytest.mark.anyio
async def test_scan_all_websites_uses_settings_changed_during_the_run(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website put on cooldown after the run started is skipped, as it is checked again just before its
    turn."""
    website = _due_website(populated_website, "https://cooldown.com", "cooldown@gmail.com")
    cooled_down = website.model_copy(update={"on_cooldown_until": datetime.now(UTC) + timedelta(hours=2)})
    mocker.patch.object(WebsiteService, "get_all", return_value=[website])
    mocker.patch.object(WebsiteService, "get", return_value=cooled_down)
    mock_scan_website = mocker.patch("app.backend.scanning.all_websites_scan.scan_website")

    assert await scan_all_websites() is None

    mock_scan_website.assert_not_called()


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_still_scans_inactive_websites(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests an inactive website is still scanned (for its critical pages) and its report returned."""
    inactive = _due_website(populated_website, "https://inactive.com", "inactive@gmail.com").model_copy(
        update={"active": False}
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[inactive])
    mock_scan_website = mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website", return_value=_scan_finding("https://inactive.com/new")
    )
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [inactive]
    assert result is not None and "https://inactive.com/new" in result


@pytest.fixture
def scan_uses_test_database(session: Session, mocker: MockerFixture, backend_uses_test_session: None) -> None:
    """Makes a scan of all websites read and save websites, scans and reports in the test's database, recording
    failed scans and emailing the reports for real (to the fake email sender)."""
    mocker.patch("app.backend.scanning.all_websites_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.all_websites_scan.record_scan", record_scan)
    mocker.patch("app.backend.scanning.all_websites_scan.send_reports_awaiting_email", send_reports_awaiting_email)


def _add_website(session: Session, url: str, recipient_emails: list[str]) -> WebsiteRead:
    """Adds a website with the given recipients."""
    return WebsiteService(session).create(WebsiteCreate(url=HttpUrl(url), recipient_emails=recipient_emails))


@pytest.mark.anyio
@pytest.mark.usefixtures("scan_uses_test_database")
async def test_scan_all_websites_emails_each_recipient_one_report_of_changes_and_failures(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
):
    """Tests a recipient of two websites is sent one email covering the changes found on one and the unexpected
    failure of the other, after which both reports are recorded as sent and both websites' scan times are saved."""
    changed = _add_website(session, "https://changed.example.com", ["both@example.com"])
    broken = _add_website(session, "https://broken.example.com", ["both@example.com"])

    async def scan(client: object, website: WebsiteRead) -> ScanRunRead:
        if website.id == broken.id:
            raise RuntimeError("Unexpected scan failure")
        return record_scan(
            session,
            website,
            ScanStatus.SUCCESS,
            changes=[ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(f"{website.url}new"))],
        )

    mocker.patch("app.backend.scanning.all_websites_scan.scan_website", side_effect=scan)
    before = datetime.now(UTC)

    await scan_all_websites()

    [email] = email_sender.sent
    assert email.to == "both@example.com"
    assert email.subject == "Website updates: changed.example.com and broken.example.com"
    assert "https://changed.example.com/new" in email.html_body
    assert "Scan Failure Report" in email.html_body
    assert ScanRunService(session).get_awaiting_email() == []
    for website in [changed, broken]:
        last_scan_at = WebsiteService(session).get(website.id).last_scan_at
        assert last_scan_at is not None and before <= last_scan_at <= datetime.now(UTC)


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
@pytest.mark.parametrize(
    "error",
    [ScanAlreadyQueuedError("https://busy.com/"), ScanCancelledError("https://busy.com/")],
    ids=["already-being-scanned", "cancelled"],
)
async def test_scan_all_websites_skips_a_website_already_being_scanned_or_cancelled(
    populated_website: WebsiteRead, mocker: MockerFixture, mock_record_scan: MagicMock, error: Exception
):
    """Tests a website already being scanned (e.g. by "Run Scan Now"), or whose scan is cancelled, is skipped without
    a failure report or a saved scan time, so it is tried again next run, and the other websites are still scanned."""
    busy = _due_website(populated_website, "https://busy.com", "busy@gmail.com")
    other = _due_website(populated_website, "https://other.com", "other@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[busy, other])
    mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website",
        side_effect=[error, _scan_finding("https://other.com/new")],
    )
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    mock_record_scan.assert_not_called()
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [other.id]
    assert result is not None and "https://other.com/new" in result and "https://busy.com" not in result


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_continues_when_a_failed_scan_cannot_be_recorded(
    populated_website: WebsiteRead, mocker: MockerFixture, mock_record_scan: MagicMock, mock_send_reports: MagicMock
):
    """Tests a database error while recording a website's failed scan is logged rather than raised, so the other
    websites are still scanned and the reports are still emailed."""
    broken = _due_website(populated_website, "https://broken.com", "broken@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[broken, last])
    mocker.patch(
        "app.backend.scanning.all_websites_scan.scan_website",
        side_effect=[RuntimeError("Unexpected scan failure"), _scan_finding("https://last.com/new")],
    )
    mock_record_scan.side_effect = RuntimeError("database is locked")
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert result is not None and "https://last.com/new" in result and "Scan Failure Report" not in result
    mock_send_reports.assert_called_once()


# ======================================
# Cancelling Run All Scans
# ======================================


def test_cancel_run_all_does_nothing_when_run_all_scans_is_not_running() -> None:
    """Tests cancelling says so when there is no "Run All Scans" to cancel."""
    assert not run_all_scans.in_progress
    assert not run_all_scans.cancel()


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_cancelling_run_all_scans_skips_the_rest_but_still_emails_changes_found(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
    mock_send_reports: MagicMock,
):
    """Tests cancelling "Run All Scans" skips the websites still to come, while the changes already found are
    still emailed, and the cancelled website's scan time is not recorded."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    cancelled = _due_website(populated_website, "https://cancelled.com", "cancelled@gmail.com")
    skipped = _due_website(populated_website, "https://skipped.com", "skipped@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, cancelled, skipped])
    cancel_results: list[bool] = []

    async def scan(client: object, website: WebsiteRead) -> ScanRunRead:
        if website.url == first.url:
            return _scan_finding("https://first.com/new")
        assert run_all_scans.in_progress
        cancel_results.append(run_all_scans.cancel())  # Cancelled while this website is being scanned
        raise ScanCancelledError(str(website.url))

    mock_scan_website = mocker.patch("app.backend.scanning.all_websites_scan.scan_website", side_effect=scan)
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites_now()

    assert cancel_results == [True]
    assert [call.args[1].url for call in mock_scan_website.call_args_list] == [first.url, cancelled.url]
    mock_send_reports.assert_called_once()
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [first.id]
    assert result is not None and "https://first.com/new" in result
    assert not run_all_scans.in_progress


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_cancelling_run_all_scans_cancels_the_website_being_scanned(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
    empty_scan_queue: ScanQueue,
):
    """Tests cancelling "Run All Scans" stops the website being crawled, so nothing from it is saved."""
    scanning = _due_website(populated_website, "https://scanning.com", "scanning@gmail.com")
    skipped = _due_website(populated_website, "https://skipped.com", "skipped@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[scanning, skipped])
    crawls_started: list[str] = []
    crawl_started = asyncio.Event()

    async def crawl(client: object, website: WebsiteRead, *args: object) -> None:
        crawls_started.append(str(website.url))
        crawl_started.set()
        await asyncio.Event().wait()  # Runs until cancelled

    mocker.patch("app.backend.scanning.website_scan._find_updates", side_effect=crawl)
    mock_update = mocker.patch.object(WebsiteService, "update")
    run_all = asyncio.create_task(scan_all_websites_now())
    await crawl_started.wait()

    assert run_all_scans.cancel()
    assert not run_all_scans.cancel()  # Already cancelled

    assert await run_all is None
    assert crawls_started == [str(scanning.url)]
    mock_update.assert_not_called()
    assert empty_scan_queue.queued_urls == []
    assert not run_all_scans.in_progress
