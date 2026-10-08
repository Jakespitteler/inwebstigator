import uuid
from datetime import datetime, timedelta

import pytest
from pytest_mock import MockerFixture

from app.core.config import config
from app.core.errors import NotFoundError
from app.db.services.website_service import WebsiteService
from app.models.website_models import WebsiteRead
from app.scanning.all_websites_scan import is_due_a_scan, scan_all_websites


@pytest.fixture
def websites_unchanged_during_run(mocker: MockerFixture) -> None:
    """Makes a scan of all websites use each website as it was listed, as if none changed during the run."""

    def as_listed(website: WebsiteRead) -> WebsiteRead:
        return website

    mocker.patch("app.scanning.all_websites_scan._latest_state", side_effect=as_listed)


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_skips_cooldown_and_recent_scans(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests that websites on active cooldown, or websites scanned too recently are skipped."""
    cooldown_site = populated_website.model_copy(
        update={
            "id": 2,
            "active": True,
            "url": "https://cooldown.com",
            "on_cooldown_until": datetime.now() + timedelta(days=1),
        }
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "id": 3,
            "active": True,
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(),
            "days_between_scans": 7,
        }
    )

    mocker.patch.object(
        WebsiteService,
        "get_all",
        return_value=[cooldown_site, recently_scanned_site],
    )
    mock_scan_website = mocker.patch("app.scanning.all_websites_scan.scan_website")
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    mock_scan_website.assert_not_called()
    mock_send_notification.assert_not_called()
    assert result is None


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_ignoring_the_schedule_scans_websites_not_due_but_skips_cooldown(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests "Run All Scans" also scans websites scanned too recently, while websites on cooldown are still skipped."""
    cooldown_site = populated_website.model_copy(
        update={"url": "https://cooldown.com", "on_cooldown_until": datetime.now() + timedelta(days=1)}
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(),
            "days_between_scans": 7,
        }
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[cooldown_site, recently_scanned_site])
    mock_scan_website = mocker.patch("app.scanning.all_websites_scan.scan_website", return_value=None)
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
):
    """Tests one website's unexpected failure does not stop the others being scanned and reported, and its
    recipients are told the scan failed rather than being left to think nothing changed."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    broken = _due_website(populated_website, "https://broken.com", "broken@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, broken, last])
    mock_scan_website = mocker.patch(
        "app.scanning.all_websites_scan.scan_website",
        side_effect=["<li>first report</li>", RuntimeError("Unexpected scan failure"), "<li>last report</li>"],
    )
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert mock_scan_website.call_count == 3
    sent_to = [call.args[0].to for call in mock_send_notification.call_args_list]
    assert sent_to == ["first@gmail.com", "broken@gmail.com", "last@gmail.com"]
    assert [call.args[0].subject for call in mock_send_notification.call_args_list] == [
        "Website update: first.com",
        "Website update: broken.com",
        "Website update: last.com",
    ]
    failure_report: str = mock_send_notification.call_args_list[1].args[0].html_body
    assert "Scan Failure Report" in failure_report
    assert "https://broken.com" in failure_report
    assert result is not None
    assert result.startswith("<li>first report</li>")
    assert result.endswith("<li>last report</li>")

    # The broken website's scan time is still recorded, so it is retried at its normal interval
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [first.id, broken.id, last.id]


@pytest.mark.anyio
async def test_scan_all_websites_continues_after_a_database_error(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website that cannot be read, or whose scan time cannot be saved, does not end the run, so the
    other websites are still scanned and the reports already found are still emailed."""
    unreadable = _due_website(populated_website, "https://unreadable.com", "unreadable@gmail.com")
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[unreadable, first, last])

    def get_latest(website: WebsiteRead) -> WebsiteRead:
        if website.id == unreadable.id:
            raise RuntimeError("database is locked")
        return website

    mocker.patch("app.scanning.all_websites_scan._latest_state", side_effect=get_latest)
    mock_scan_website = mocker.patch(
        "app.scanning.all_websites_scan.scan_website", side_effect=["<li>first report</li>", "<li>last report</li>"]
    )
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mocker.patch.object(WebsiteService, "update", side_effect=[RuntimeError("database is locked"), None])

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [first, last]
    assert [call.args[0].to for call in mock_send_notification.call_args_list] == ["first@gmail.com", "last@gmail.com"]
    assert result == "<li>first report</li><li>last report</li>"


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_reads_every_website(mocker: MockerFixture):
    """Tests every website is read for the run, not just the first page of 100."""
    mock_get_all = mocker.patch.object(WebsiteService, "get_all", return_value=[])

    await scan_all_websites()

    mock_get_all.assert_called_once_with(limit=None)


@pytest.mark.parametrize(
    ("days_between_scans", "last_scan_at", "run_started_at", "expected_due"),
    [
        (1, None, datetime(2026, 1, 1, 8, 0), True),
        (1, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 8, 0), False),
        (1, datetime(2026, 1, 1, 8, 0, 5), datetime(2026, 1, 2, 8, 0, 2), True),
        (1.5, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 20, 0), False),
        (1.5, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 3, 8, 0), True),
        (0.5, datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 20, 0), True),
        (0.5, datetime(2026, 1, 1, 15, 0), datetime(2026, 1, 1, 20, 0), False),
    ],
    ids=[
        "never-scanned",
        "a-day-not-yet-passed",
        "run-started-a-few-seconds-earlier-than-last-time",
        "a-day-and-a-half-not-yet-passed",
        "a-day-and-a-half-passed",
        "half-a-day-passed",
        "half-a-day-not-yet-passed",
    ],
)
def test_is_due_a_scan_counts_from_the_exact_time_of_the_last_scan(
    populated_website: WebsiteRead,
    days_between_scans: float,
    last_scan_at: datetime | None,
    run_started_at: datetime,
    expected_due: bool,
):
    """Tests a website is due a scan once its interval has passed since its last scan, so intervals such as 1.5
    days are not rounded down to whole days, while a run starting slightly early still counts."""
    website = populated_website.model_copy(
        update={"days_between_scans": days_between_scans, "last_scan_at": last_scan_at}
    )

    assert is_due_a_scan(website, run_started_at) is expected_due


def test_is_due_a_scan_tolerance_is_at_most_half_the_time_between_runs(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests that when runs are minutes apart, a website set to scan every half hour is scanned at the run nearest
    to when it is due, rather than at every run because of the usual hour's tolerance."""
    mocker.patch.object(config, "scheduler_minimum_days_between_scans", 0.0034)  # Runs every ~4.9 minutes
    last_scan_at = datetime(2026, 1, 1, 8, 0)
    website = populated_website.model_copy(update={"days_between_scans": 0.02, "last_scan_at": last_scan_at})
    runs = [last_scan_at + timedelta(days=0.0034) * run for run in range(1, 8)]

    due_runs = [run for run in runs if is_due_a_scan(website, run)]

    assert due_runs[0] == runs[5]  # The 6th run, 29.4 minutes after the last scan, is the nearest to 28.8 minutes


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_continues_after_a_failed_send(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests one recipient's failed email does not stop the remaining recipients being emailed."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    second = _due_website(populated_website, "https://second.com", "second@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, second])
    mocker.patch(
        "app.scanning.all_websites_scan.scan_website", side_effect=["<li>first report</li>", "<li>second report</li>"]
    )
    mock_send_notification = mocker.patch(
        "app.scanning.notifications.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert [call.args[0].to for call in mock_send_notification.call_args_list] == [
        "first@gmail.com",
        "second@gmail.com",
    ]
    assert result == "<li>first report</li><li>second report</li>"


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_returns_reports_for_websites_without_recipients(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a dashboard-only website (no recipients) still has its report returned, without sending email."""
    website = _due_website(populated_website, "https://dashboard-only.com", "unused@gmail.com").model_copy(
        update={"recipients": []}
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[website])
    mocker.patch("app.scanning.all_websites_scan.scan_website", return_value="<li>dashboard report</li>")
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    mock_send_notification.assert_not_called()
    assert result == "<li>dashboard report</li>"


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
    mock_scan_website = mocker.patch("app.scanning.all_websites_scan.scan_website", return_value="<li>kept report</li>")
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [kept]
    assert [call.args[0].to for call in mock_send_notification.call_args_list] == ["kept@gmail.com"]
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [kept.id]
    assert result == "<li>kept report</li>"


@pytest.mark.anyio
async def test_scan_all_websites_uses_settings_changed_during_the_run(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website put on cooldown after the run started is skipped, as it is checked again just before its
    turn."""
    website = _due_website(populated_website, "https://cooldown.com", "cooldown@gmail.com")
    cooled_down = website.model_copy(update={"on_cooldown_until": datetime.now() + timedelta(hours=2)})
    mocker.patch.object(WebsiteService, "get_all", return_value=[website])
    mocker.patch.object(WebsiteService, "get", return_value=cooled_down)
    mock_scan_website = mocker.patch("app.scanning.all_websites_scan.scan_website")

    assert await scan_all_websites() is None

    mock_scan_website.assert_not_called()


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_still_scans_inactive_websites(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests an inactive website is still scanned (for its critical pages) and its report emailed."""
    inactive = _due_website(populated_website, "https://inactive.com", "inactive@gmail.com").model_copy(
        update={"active": False}
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[inactive])
    mock_scan_website = mocker.patch(
        "app.scanning.all_websites_scan.scan_website", return_value="<li>critical page report</li>"
    )
    mock_send_notification = mocker.patch("app.scanning.notifications.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [inactive]
    assert [call.args[0].to for call in mock_send_notification.call_args_list] == ["inactive@gmail.com"]
    assert result == "<li>critical page report</li>"
