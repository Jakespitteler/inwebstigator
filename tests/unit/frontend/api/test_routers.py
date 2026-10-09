import asyncio
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient, MockTransport, Response
from pydantic import BaseModel, HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.scanning.scan_queue import ScanQueue, scan_queue
from app.core.config import config
from app.core.errors import ScanAlreadyQueuedError, ScanCancelledError, UndeliverableEmailError, WebsiteTooLargeError
from app.core.paths import resource_path
from app.db.schema import Base, DBChange, DBCriticalPage, DBRecipient, DBScanRun, DBWebsite
from app.db.services.scan_run_service import ScanRunService
from app.db.session import get_db_session
from app.frontend.api import routers
from app.frontend.api.request_guard import API_TOKEN_HEADER
from app.main import app
from app.models import critical_page_models, recipient_models, website_models
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunCreate, ScanRunRead, ScanStatus
from tests.fakes import FakeEmailSender
from tests.unit.backend.email_service.builders import make_scan_run


class TestCRUDRouters:
    """
    Base test suite for standard Router operations.
    Subclasses must define prefix, model_create, model_update, and fixture_name.
    """

    __test__ = False

    prefix: str
    model_create: BaseModel
    model_update: BaseModel
    fixture_name: str
    expected_create_status: int = 201

    def create_payload(self, request: pytest.FixtureRequest) -> dict[str, Any]:
        """Returns the body of the request that creates a record. A subclass can fill in IDs from its fixtures."""
        return self.model_create.model_dump(mode="json")

    @pytest.fixture
    def api_record(self, request: pytest.FixtureRequest) -> Base:
        """Dynamically fetches the database record fixture required by the subclass."""
        return request.getfixturevalue(self.fixture_name)

    def test_get_all_records(self, api_client: TestClient, api_record: Base) -> None:
        """
        Tests retrieving a list of records from an api router includes an existing record.

        Args:
            api_client: The FastAPI test client.
            api_record: An existing record.
        """
        response: Response = api_client.get(url=self.prefix)
        assert response.status_code == 200, response.text
        assert str(api_record.id) in [record["id"] for record in response.json()]

    def test_get_record(self, api_client: TestClient, api_record: Base) -> None:
        """
        Tests retrieving an existing record by its ID.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
            test_api_record: An existing record.
        """
        response: Response = api_client.get(url=f"{self.prefix}/{api_record.id}")
        assert response.status_code == 200, response.text
        assert response.json()["id"] == str(api_record.id)

    def test_get_record_not_found(self, api_client: TestClient) -> None:
        """
        Tests that retrieving a non-existent record returns a 404 status.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.get(url=f"{self.prefix}/{uuid.uuid4()}")
        assert response.status_code == 404, response.text

    def test_create_record(self, api_client: TestClient, request: pytest.FixtureRequest) -> None:
        """
        Tests creating a new record.

        Args:
            api_client: The FastAPI test client.
            request: Gives the subclass access to its fixtures.
        """
        response: Response = api_client.post(url=self.prefix, json=self.create_payload(request))
        assert response.status_code == self.expected_create_status, response.text
        if response.status_code == 201:  # The new record is saved, so it can be read back
            assert api_client.get(url=f"{self.prefix}/{response.json()['id']}").status_code == 200

    def test_update_record(self, api_client: TestClient, api_record: Base) -> None:
        """
        Tests updating an existing record's details saves the new details.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
            test_api_record: An existing record.
        """
        changes: dict[str, Any] = self.model_update.model_dump(mode="json", exclude_unset=True)
        response: Response = api_client.patch(url=f"{self.prefix}/{api_record.id}", json=changes)
        assert response.status_code == 200, response.text
        assert response.json() | changes == response.json()

    def test_update_record_not_found(self, api_client: TestClient) -> None:
        """
        Tests that updating a non-existent record returns a 404 status.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.patch(
            url=f"{self.prefix}/{uuid.uuid4()}",
            json=self.model_update.model_dump(mode="json"),
        )
        assert response.status_code == 404, response.text

    def test_delete_record(self, api_client: TestClient, api_record: Base) -> None:
        """
        Tests deleting an existing record.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
            test_api_record: An existing record.
        """
        response: Response = api_client.delete(url=f"{self.prefix}/{api_record.id}")
        assert response.status_code == 204, response.text

        fetch_response = api_client.get(url=f"{self.prefix}/{api_record.id}")
        assert fetch_response.status_code == 404

    def test_delete_record_not_found(self, api_client: TestClient) -> None:
        """
        Tests that deleting a non-existent record returns a 404 status.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.delete(url=f"{self.prefix}/{uuid.uuid4()}")
        assert response.status_code == 404, response.text


# ==========================
#  Test Implementations
# ==========================


class TestRecipientRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.RECIPIENT_ROUTER.prefix
    model_create = recipient_models.RecipientCreate(email="test_recipient@gmail.com")
    model_update = recipient_models.RecipientUpdate(email="test_recipient_update@gmail.com")
    fixture_name = "test_recipient"


class TestWebsiteRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.WEBSITE_ROUTER.prefix
    model_create = website_models.WebsiteCreate(url=HttpUrl("https://www.test_website.com"))
    model_update = website_models.WebsiteSettingsUpdate(days_between_scans=2)
    fixture_name = "test_website"
    # Websites are only added through /scanner/initial_scan, which checks the website and saves its baseline
    expected_create_status = 405

    def test_update_only_accepts_settings(self, api_client: TestClient, api_record: Base) -> None:
        """Tests fields the scanner saves (e.g. its failure count) cannot be changed through the API."""
        response: Response = api_client.patch(
            url=f"{self.prefix}/{api_record.id}", json={"failed_attempts_at_min_speed": 99, "days_between_scans": 3}
        )

        assert response.status_code == 200, response.text
        assert response.json()["failed_attempts_at_min_speed"] == 0
        assert response.json()["days_between_scans"] == 3


class TestCriticalPageRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.CRITICAL_PAGE_ROUTER.prefix
    model_create = critical_page_models.CriticalPageCreate(
        url=HttpUrl("https://www.test_website.com/test_critical_page"),
        website_id=uuid.uuid4(),
    )
    model_update = critical_page_models.CriticalPageSettingsUpdate(ignore_rules=[re.compile(r"Last updated .*")])
    fixture_name = "test_critical_page"
    # Critical pages are only added through /scanner/initial_critical_page_scan, which checks the page is on the
    # website and loads, and saves its baseline
    expected_create_status = 405

    def create_payload(self, request: pytest.FixtureRequest) -> dict[str, Any]:
        """Creates the critical page on a website that exists, as foreign keys are checked."""
        website: website_models.WebsiteRead = request.getfixturevalue("test_website")
        return self.model_create.model_copy(update={"website_id": website.id}).model_dump(mode="json")

    def test_update_only_accepts_settings(self, api_client: TestClient, api_record: Base) -> None:
        """Tests the page's saved copy, which the scanner compares against, cannot be changed through the API."""
        response: Response = api_client.patch(url=f"{self.prefix}/{api_record.id}", json={"text_body": "<p>Fake</p>"})

        assert response.status_code == 200, response.text
        assert response.json()["text_body"] is None


# ==========================
#  Dashboard
# ==========================


def _next_check_text(api_client: TestClient) -> str | None:
    """Returns the dashboard's "Next scheduled check" line, or None if it is not shown."""
    response = api_client.get("/")
    assert response.status_code == 200, response.text
    line = BeautifulSoup(response.text, "html.parser").select_one(".week-range")
    return " ".join(line.get_text().split()) if line else None


def _last_scan_by_website(api_client: TestClient) -> dict[str, str]:
    """Returns each website card's "Last scanned" line, keyed by its full URL."""
    response = api_client.get("/")
    assert response.status_code == 200, response.text
    cards = BeautifulSoup(response.text, "html.parser").select(".website-card")
    scan_times: dict[str, str] = {}
    for card in cards:
        button = card.select_one(".run-scan-button")
        timestamp = card.select_one(".last-scan")
        assert button is not None and timestamp is not None
        scan_times[str(button["data-website-url"])] = " ".join(timestamp.get_text().split())
    return scan_times


def test_dashboard_shows_each_websites_own_scan_time(api_client: TestClient, session: Session) -> None:
    """Tests every website card shows when that website was last scanned, not the latest scan overall."""
    session.add_all(
        [
            DBWebsite(url="https://older.example.com", last_scan_at=datetime(2026, 9, 28, 9, 5).astimezone()),
            DBWebsite(url="https://newer.example.com", last_scan_at=datetime(2026, 10, 1, 14, 30).astimezone()),
            DBWebsite(url="https://never-scanned.example.com"),
        ]
    )
    session.flush()

    assert _last_scan_by_website(api_client) == {
        "https://older.example.com/": "Last scanned 28 Sep 2026, 09:05",
        "https://newer.example.com/": "Last scanned 01 Oct 2026, 14:30",
        "https://never-scanned.example.com/": "Not scanned yet",
    }


def test_favicon_is_the_apps_icon(api_client: TestClient) -> None:
    """Tests the browser is given the app's own icon for the window and tab."""
    response = api_client.get("/favicon.ico")

    assert response.status_code == 200, response.text
    assert response.content == resource_path("app", "frontend", "static", "favicon.ico").read_bytes()


def _dashboard_static_paths(api_client: TestClient) -> list[str]:
    """Returns the path of each of its own stylesheets and scripts the dashboard loads, e.g. "/static/js/scans.js"."""
    page = BeautifulSoup(api_client.get("/").text, "html.parser")
    urls: list[str] = [
        str(tag.get("href") or tag.get("src")) for tag in page.select('link[rel="stylesheet"], script[src]')
    ]
    return [urlsplit(url).path for url in urls if "/static/" in url]


def test_every_stylesheet_and_script_the_dashboard_loads_is_served(api_client: TestClient) -> None:
    """Tests each style and script file the dashboard asks for is found, as a missing one quietly breaks the page."""
    for path in _dashboard_static_paths(api_client):
        assert api_client.get(path).status_code == 200, path


def test_the_dashboard_loads_every_stylesheet_and_script(api_client: TestClient) -> None:
    """Tests no style or script file is left out of the dashboard, e.g. a new one that was never linked to."""
    static_folder = resource_path("app", "frontend", "static")
    static_files: list[str] = [
        f"/static/{path.relative_to(static_folder).as_posix()}"
        for path in static_folder.rglob("*")
        if path.suffix in {".css", ".js"}
    ]

    assert sorted(_dashboard_static_paths(api_client)) == sorted(static_files)


def test_dashboard_shows_next_scheduled_check(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests the dashboard shows when the scheduler next checks which websites are due a scan."""
    mocker.patch(
        "app.frontend.api.routers.next_scheduled_check", return_value=datetime(2026, 10, 5, 21, 30).astimezone()
    )

    assert _next_check_text(api_client) == "Next scheduled check: 05 Oct 2026, 21:30"


def test_dashboard_hides_next_check_when_scans_are_not_scheduled(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests the "Next scheduled check" line is left out when automatic scans are not running."""
    mocker.patch("app.frontend.api.routers.next_scheduled_check", return_value=None)

    assert _next_check_text(api_client) is None


def _scan(
    url: str, scanned_at: datetime, new_link: str | None = None, status: ScanStatus = ScanStatus.SUCCESS
) -> DBScanRun:
    """Creates a scan of a website, which found a new link on its main page if one is given."""
    changes = [DBChange(position=0, kind=ChangeKind.LINK_ADDED, page_url=url, url=new_link)] if new_link else []
    return DBScanRun(scanned_at=scanned_at, status=status, changes=changes)


def test_updates_are_listed_most_recent_change_first(api_client: TestClient, session: Session) -> None:
    """Tests websites are listed most recent change first, and each website's scans newest first, with when each
    ran and what it found."""
    session.add_all(
        [
            DBWebsite(
                url="https://older.example.com",
                scan_runs=[
                    _scan(
                        "https://older.example.com/",
                        datetime(2026, 10, 1, 9, 0).astimezone(),
                        "https://older.example.com/a",
                    ),
                    _scan("https://older.example.com/", datetime(2026, 10, 5, 9, 0).astimezone()),  # Found nothing
                ],
            ),
            DBWebsite(
                url="https://newer.example.com",
                scan_runs=[
                    _scan(
                        "https://newer.example.com/",
                        datetime(2026, 10, 2, 9, 0).astimezone(),
                        "https://newer.example.com/b",
                    ),
                    _scan(
                        "https://newer.example.com/",
                        datetime(2026, 10, 4, 9, 0).astimezone(),
                        "https://newer.example.com/c",
                    ),
                ],
            ),
            DBWebsite(url="https://never-scanned.example.com"),
        ]
    )
    session.flush()

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    website_urls = [link["href"] for link in dashboard.select(".website-change-record .website-url")]
    scan_times = [" ".join(time.get_text().split()) for time in dashboard.select(".scan-change-record .change-time")]
    new_links = [link.get_text(strip=True) for link in dashboard.select(".page-change-record .change-item-added")]

    assert website_urls == ["https://newer.example.com/", "https://older.example.com/"]
    assert scan_times == [
        "Scanned 04 Oct 2026, 09:00",
        "Scanned 02 Oct 2026, 09:00",
        "Scanned 05 Oct 2026, 09:00",
        "Scanned 01 Oct 2026, 09:00",
    ]
    assert new_links == [
        "https://newer.example.com/cAdded",
        "https://newer.example.com/bAdded",
        "https://older.example.com/aAdded",
    ]


def test_updates_show_only_the_latest_scans_and_which_are_waiting_to_be_emailed(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests each website shows only its most recent scans, says how a failed scan went, and marks a report whose
    email has not been sent yet."""
    mocker.patch.object(config, "scans_kept_per_website", 2)
    url = "https://example.com/"
    session.add(
        DBWebsite(
            url=url,
            scan_runs=[
                _scan(url, datetime(2026, 10, 1, 9, 0).astimezone(), "https://example.com/too-old-to-show"),
                DBScanRun(
                    scanned_at=datetime(2026, 10, 2, 9, 0).astimezone(),
                    status=ScanStatus.CONNECTION_ERROR,
                    message="Website placed on cooldown.",
                ),
                _scan(url, datetime(2026, 10, 3, 9, 0).astimezone(), "https://example.com/emailed"),
            ],
        )
    )
    session.flush()
    session.execute(
        update(DBScanRun)
        .where(DBScanRun.scanned_at > datetime(2026, 10, 2, 12, 0).astimezone())
        .values(notified_at=datetime(2026, 10, 3, 9, 1).astimezone())
    )

    updates_panel = BeautifulSoup(api_client.get("/").text, "html.parser").select_one("#updates-panel")
    assert updates_panel is not None
    scans = [" ".join(scan.get_text().split()) for scan in updates_panel.select(".scan-change-record")]

    assert "Last 2 scans of each website" in updates_panel.get_text()
    assert len(scans) == 2
    assert "https://example.com/emailed" in scans[0] and "Email not sent yet" not in scans[0]
    assert "Could not connect" in scans[1] and "Website placed on cooldown." in scans[1]
    assert "Email not sent yet" in scans[1]
    assert "too-old-to-show" not in updates_panel.get_text()


def test_hosted_names_are_used_in_compact_cards_and_updates(api_client: TestClient, session: Session) -> None:
    """Tests a website's name is read from its saved home page, including for sites on a hosting provider's
    subdomain, and is shown on both its card and its updates."""
    url = "https://webloom-two.vercel.app/test-site"
    session.add(
        DBWebsite(
            url=url,
            critical_pages=[DBCriticalPage(url=url, text_body="<title>Webloom Two</title>")],
            scan_runs=[_scan(url, datetime(2026, 10, 1, 9, 0).astimezone(), "https://webloom-two.vercel.app/new")],
        )
    )
    session.flush()

    response = api_client.get("/")

    assert response.status_code == 200
    dashboard = BeautifulSoup(response.text, "html.parser")
    card = dashboard.select_one(".website-card")
    update = dashboard.select_one(".website-change-record")
    assert card is not None and update is not None
    card_name = card.select_one(".website-name")
    update_name = update.select_one(".website-name")
    website_link = card.select_one(".website-meta .website-url")
    last_scan = card.select_one(".website-meta .last-scan")
    assert card_name is not None and update_name is not None
    assert website_link is not None and last_scan is not None
    assert card_name.get_text(strip=True) == "Webloom Two - Test Site"
    assert update_name.get_text(strip=True) == "Webloom Two - Test Site"
    assert card["data-website-name"] == "Webloom Two - Test Site"
    assert website_link["href"] == url
    assert website_link.get_text(strip=True) == "/test-site"
    assert last_scan.get_text(strip=True) == "Not scanned yet"


def test_run_all_scans_every_website(
    api_client: TestClient, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests "Run All Scans" scans every website, due or not."""
    mock_scan_all_websites_now = mocker.patch("app.frontend.api.routers.scan_all_websites_now", return_value=None)

    response = api_client.post("/scanner/run_all")

    assert response.status_code == 200, response.text
    mock_scan_all_websites_now.assert_awaited_once_with(email_sender=email_sender)


def test_run_all_is_refused_while_run_all_scans_is_already_running(
    api_client: TestClient, mocker: MockerFixture
) -> None:
    """Tests a second "Run All Scans" is refused rather than run alongside the first."""
    mocker.patch("app.frontend.api.routers.run_all_in_progress", return_value=True)
    mock_scan_all_websites_now = mocker.patch("app.frontend.api.routers.scan_all_websites_now")

    response = api_client.post("/scanner/run_all")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Run All Scans is already running."
    mock_scan_all_websites_now.assert_not_called()


def test_cancel_all_reports_whether_run_all_scans_was_cancelled(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests "Cancel" on Run All Scans cancels it, and says so when it wasn't running."""
    mocker.patch("app.frontend.api.routers.cancel_run_all", side_effect=[True, False])

    assert api_client.post("/scanner/cancel_all").json() is True
    assert api_client.post("/scanner/cancel_all").json() is False


@pytest.mark.parametrize("running", [True, False])
def test_dashboard_shows_run_all_cancel_button_only_while_run_all_scans_is_running(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture, running: bool
) -> None:
    """Tests the Cancel button for Run All Scans shows, and Run All Scans is disabled, only while it is running,
    even after a refresh."""
    mocker.patch("app.frontend.api.routers.run_all_in_progress", return_value=running)

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")

    run_all_button = dashboard.select_one("#run-all-scans-button")
    cancel_button = dashboard.select_one("#cancel-all-scans-button")
    assert run_all_button is not None and cancel_button is not None
    assert run_all_button.has_attr("disabled") is running
    assert cancel_button.has_attr("hidden") is not running


def test_background_scan_note_can_be_closed_for_now_or_for_good(
    api_client: TestClient, test_website: website_models.WebsiteRead
) -> None:
    """Tests the "you can close this window" note starts hidden, and has a cross to hide it until the next scan
    as well as a separate "Don't show again" button to stop it showing again."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")

    note = dashboard.select_one("#background-scan-note")
    assert note is not None and note.has_attr("hidden")  # Only shown while a scan runs

    close_button = note.select_one("#close-background-scan-note")
    assert close_button is not None
    assert close_button.get("type") == "button"  # Doesn't submit anything
    assert close_button.get("aria-label") == "Hide this note until the next scan"

    dismiss_button = note.select_one("#hide-background-scan-note")
    assert dismiss_button is not None
    assert dismiss_button.get("type") == "button"
    assert " ".join(dismiss_button.get_text().split()) == "Don't show again"


def test_manual_scan_records_scan_time(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Run Scan Now" records when the website was scanned, so the dashboard and scheduler see it."""
    mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=make_scan_run())
    before = datetime.now(UTC)

    response = api_client.post("/scanner/run", data={"url": str(test_website.url)})

    assert response.status_code == 200, response.text
    website = session.get(DBWebsite, test_website.id)
    assert website
    last_scan_at = website.last_scan_at
    assert last_scan_at is not None and last_scan_at >= before
    assert _last_scan_by_website(api_client)[str(test_website.url)] == (
        f"Last scanned {last_scan_at.astimezone():%d %b %Y, %H:%M}"
    )


def _scan_finding_a_new_page(session: Session, website: website_models.WebsiteRead) -> ScanRunRead:
    """Adds a scan to the website's history that found a new page, as "Run Scan Now" would."""
    return ScanRunService(session).create(
        ScanRunCreate(
            website_id=website.id,
            scanned_at=datetime.now(UTC),
            changes=[ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(f"{website.url}new-page"))],
        )
    )


def test_manual_scan_emails_its_report_and_records_it_as_sent(
    api_client: TestClient,
    session: Session,
    test_website: website_models.WebsiteRead,
    mocker: MockerFixture,
    email_sender: FakeEmailSender,
) -> None:
    """Tests "Run Scan Now" emails the scan's report to the website's recipients, and records it as sent so the next
    scheduled run does not send it again."""
    scan_run: ScanRunRead = _scan_finding_a_new_page(session, test_website)
    mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=scan_run)

    response = api_client.post("/scanner/run", data={"url": str(test_website.url)})

    assert response.status_code == 200, response.text
    assert [email.to for email in email_sender.sent] == [recipient.email for recipient in test_website.recipients]
    assert f"{test_website.url}new-page" in email_sender.sent[0].html_body
    assert ScanRunService(session).get_awaiting_email() == []


def test_manual_scan_keeps_its_report_when_the_email_fails(
    api_client: TestClient,
    session: Session,
    test_website: website_models.WebsiteRead,
    mocker: MockerFixture,
    email_sender: FakeEmailSender,
) -> None:
    """Tests a "Run Scan Now" report that could not be emailed is kept, so the next scheduled run sends it."""
    scan_run: ScanRunRead = _scan_finding_a_new_page(session, test_website)
    mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=scan_run)
    mocker.patch.object(email_sender, "send", side_effect=ConnectionError("No internet"))

    with pytest.raises(ConnectionError):
        api_client.post("/scanner/run", data={"url": str(test_website.url)})

    assert [waiting.id for waiting in ScanRunService(session).get_awaiting_email()] == [scan_run.id]


def test_manual_scan_of_a_website_already_queued_is_refused(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Run Scan Now" for a website already queued or being scanned is refused rather than queued twice."""
    mocker.patch.object(scan_queue, "_scans", {str(test_website.url): mocker.Mock()})
    mock_get_website_updates = mocker.patch("app.backend.scanning.website_scan.get_website_updates")

    response = api_client.post("/scanner/run", data={"url": str(test_website.url)})

    assert response.status_code == 409, response.text
    mock_get_website_updates.assert_not_called()


def test_cancel_scan_reports_whether_there_was_a_scan_to_cancel(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Cancel Scan" cancels a queued or running scan, and says so when there was nothing to cancel."""
    mock_crawl = mocker.Mock()
    mock_crawl.cancel.return_value = True
    mocker.patch.object(scan_queue, "_scans", {str(test_website.url): mock_crawl})

    assert api_client.post("/scanner/cancel", data={"url": str(test_website.url)}).json() is True
    mock_crawl.cancel.assert_called_once()
    assert api_client.post("/scanner/cancel", data={"url": "https://not-queued.com"}).json() is False


def test_dashboard_shows_cancel_button_only_for_websites_being_scanned(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests a website that is queued or being scanned shows "Cancel Scan" instead of "Run Scan Now" after a
    refresh, and other websites do not."""
    session.add(DBWebsite(url="https://not-scanning.example.com"))
    session.flush()
    mocker.patch.object(scan_queue, "_scans", {str(test_website.url): mocker.Mock()})

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")

    for url, scanning in [(test_website.url, True), ("https://not-scanning.example.com/", False)]:
        run_button = dashboard.select_one(f'.run-scan-button[data-website-url="{url}"]')
        cancel_button = dashboard.select_one(f'.cancel-scan-button[data-website-url="{url}"]')
        assert run_button is not None and cancel_button is not None
        assert run_button.has_attr("disabled") is scanning
        assert cancel_button.has_attr("hidden") is not scanning


# ==========================
#  Adding and deleting websites mid-scan
# ==========================


def test_deleting_a_website_cancels_its_scan(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests deleting a website that is queued or being scanned cancels its scan as well as deleting it."""
    mock_crawl = mocker.Mock()
    mocker.patch.object(scan_queue, "_scans", {str(test_website.url): mock_crawl})

    response = api_client.delete(f"/websites/{test_website.id}")

    assert response.status_code == 204, response.text
    mock_crawl.cancel.assert_called_once()
    assert session.get(DBWebsite, test_website.id) is None


def test_cancelling_the_first_scan_does_not_add_the_website(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests cancelling a new website's first scan cancels adding it, so no website is left without a baseline."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch(
        "app.backend.websites.website_setup.scan_website", side_effect=ScanCancelledError("https://example.com")
    )

    response = api_client.post("/scanner/initial_scan", json={"url": "https://example.com"})

    assert response.status_code == 409, response.text
    assert session.scalars(select(DBWebsite)).all() == []


def test_adding_a_website_already_being_scanned_does_not_add_a_duplicate(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests adding a website while the same website is already being added or scanned is refused, without
    leaving a duplicate website behind that has no baseline."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch(
        "app.backend.websites.website_setup.scan_website", side_effect=ScanAlreadyQueuedError("https://example.com")
    )

    response = api_client.post("/scanner/initial_scan", json={"url": "https://example.com"})

    assert response.status_code == 409, response.text
    assert session.scalars(select(DBWebsite)).all() == []


def test_adding_a_website_counts_as_emailing_its_recipients(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests a new website's recipients are recorded as emailed (they were sent the email saying what is
    monitored), so their health checks count from then."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch("app.backend.websites.website_setup.scan_website", return_value=None)

    response = api_client.post(
        "/scanner/initial_scan", json={"url": "https://example.com", "recipient_emails": ["someone@example.com"]}
    )

    assert response.status_code == 200, response.text
    recipient = session.scalars(select(DBRecipient).where(DBRecipient.email == "someone@example.com")).one()
    assert recipient.last_email_at is not None


def test_adding_a_website_sends_each_recipient_one_email(
    api_client: TestClient, session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests each recipient of a new website gets one email saying what is monitored, which also confirms their
    address, and no second email once the first scan has finished."""
    session.add(DBRecipient(email="known@example.com"))
    session.flush()
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch("app.backend.websites.website_setup.scan_website", return_value=None)
    mock_confirm = mocker.patch("app.backend.websites.recipient_checks.confirm_address_can_receive_email")
    mock_send = mocker.patch("app.backend.websites.recipient_checks.send_confirmation")

    response = api_client.post(
        "/scanner/initial_scan",
        json={"url": "https://example.com", "recipient_emails": ["known@example.com", "new@example.com"]},
    )

    assert response.status_code == 200, response.text
    emails = [*mock_send.call_args_list, *mock_confirm.call_args_list]
    sent: list[OutgoingEmail] = [email.args[0] for email in emails]
    assert sorted(email.to for email in sent) == ["known@example.com", "new@example.com"]
    for email in sent:
        assert email.subject == "Website monitoring started"
        assert "Now monitoring https://example.com" in email.html_body
    assert email_sender.sent == []  # No scan report email after the first scan


def test_adding_a_website_too_large_to_scan_deactivates_it(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests a new website too large to scan is kept but deactivated, its critical pages are still watched, and the
    dashboard says why it was deactivated."""
    main_url = "https://example.com"

    def mock_client() -> AsyncClient:
        return AsyncClient(transport=MockTransport(lambda request: Response(200, text="<p>Home page.</p>")))

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.websites.website_setup.new_http_client", side_effect=mock_client)
    mocker.patch(
        "app.backend.scanning.change_detection.crawl_site", side_effect=WebsiteTooLargeError(main_url, max_pages=50_000)
    )

    response = api_client.post(
        "/scanner/initial_scan", json={"url": main_url, "recipient_emails": ["someone@example.com"]}
    )

    assert response.status_code == 200, response.text
    website = session.scalars(select(DBWebsite)).one()
    assert website.active is False
    assert website.deactivated_reason == website_models.DeactivationReason.TOO_LARGE
    assert website.critical_pages[0].text_body == "<p>Home page.</p>"  # The main page's baseline was saved

    notice = _website_notice(api_client, main_url)
    assert notice is not None
    assert f"more than {config.web_crawler_default_max_pages:,} pages" in notice


def _website_notice(api_client: TestClient, url: str) -> str | None:
    """Returns the notice on a website's dashboard card saying why it was deactivated, or None if it has none."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    delete_button = dashboard.select_one(f'.delete-website-button[data-website-url="{HttpUrl(url)}"]')
    assert delete_button is not None
    card = delete_button.find_parent(class_="website-card")
    assert card is not None
    notice = card.select_one(".website-notice")
    return " ".join(notice.get_text().split()) if notice else None


def _run_scan_button_text(api_client: TestClient, url: str) -> str:
    """Returns the text of a website's run scan button on the dashboard."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    button = dashboard.select_one(f'.run-scan-button[data-website-url="{HttpUrl(url)}"]')
    assert button is not None
    return " ".join(button.get_text().split())


def test_dashboard_explains_inactive_websites_only_have_critical_pages_scanned(
    api_client: TestClient, session: Session
) -> None:
    """Tests an inactive website's card says only its critical pages are scanned (and why, if it was too large),
    its scan button says so too, and active websites, including re-activated ones, show neither."""
    session.add_all(
        [
            DBWebsite(url="https://too-large.example.com", active=False, deactivated_reason="too_large"),
            DBWebsite(url="https://switched-off.example.com", active=False),
            DBWebsite(url="https://active.example.com"),
        ]
    )
    session.flush()
    reactivated = DBWebsite(url="https://reactivated.example.com", active=False, deactivated_reason="too_large")
    session.add(reactivated)
    session.flush()
    api_client.patch(f"/websites/{reactivated.id}", json={"active": True})

    too_large_notice = _website_notice(api_client, "https://too-large.example.com")
    assert too_large_notice is not None
    assert too_large_notice.startswith("Too large to scan.")
    assert "Only its critical pages are checked" in too_large_notice

    switched_off_notice = _website_notice(api_client, "https://switched-off.example.com")
    assert switched_off_notice is not None
    assert switched_off_notice.startswith("Inactive.")
    assert "Only its critical pages are checked" in switched_off_notice

    for url in ["https://too-large.example.com", "https://switched-off.example.com"]:
        assert _run_scan_button_text(api_client, url) == "Scan critical pages"
    for url in ["https://active.example.com", "https://reactivated.example.com"]:
        assert _website_notice(api_client, url) is None
        assert _run_scan_button_text(api_client, url) == "Run scan"


def test_scan_settings_explain_what_inactive_means(api_client: TestClient, test_website: website_models.WebsiteRead):
    """Tests the "Active" setting explains that an inactive website still has its critical pages checked."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    help_text = dashboard.select_one(".scan-settings .scan-setting-help")

    assert help_text is not None
    assert "only have their critical pages checked" in help_text.get_text()


# ==========================
#  Cancelling a website's first scan, end to end
# ==========================


@pytest.fixture
def first_scan_in_progress(
    mocker: MockerFixture, session: Session, empty_scan_queue: ScanQueue, backend_uses_test_session: None
) -> Iterator[asyncio.Event]:
    """Makes a new website's first scan run until it is cancelled, and runs the app against the test database.

    Returns:
        An event that is set once the first scan has started crawling.
    """
    crawl_started = asyncio.Event()

    async def crawl_until_cancelled(*args: object) -> None:
        crawl_started.set()
        await asyncio.Event().wait()

    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch("app.backend.scanning.website_scan.get_website_updates", side_effect=crawl_until_cancelled)
    app.dependency_overrides[get_db_session] = lambda: session
    yield crawl_started
    app.dependency_overrides.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_by", ["cancelling the scan", "deleting the website"])
async def test_stopping_a_new_websites_first_scan_leaves_no_website(
    first_scan_in_progress: asyncio.Event, session: Session, cancel_by: str
) -> None:
    """Tests the first scan of a website being added can be stopped from the wizard's "Cancel Scan" button or by
    deleting the website, either way leaving no website behind and no scan running."""
    url = "https://example.com"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={API_TOKEN_HEADER: config.api_token}
    ) as client:
        adding = asyncio.create_task(client.post("/scanner/initial_scan", json={"url": url}))
        await first_scan_in_progress.wait()

        if cancel_by == "cancelling the scan":
            response = await client.post("/scanner/cancel", data={"url": url})
            assert response.json() is True
        else:
            website = session.scalars(select(DBWebsite).where(DBWebsite.url == HttpUrl(url))).one()
            response = await client.delete(f"/websites/{website.id}")
            assert response.status_code == 204, response.text

        async with asyncio.timeout(5):  # Fails rather than hangs if the scan was not stopped
            added = await adding

    assert added.status_code == 409, added.text
    assert session.scalars(select(DBWebsite)).all() == []
    assert scan_queue.queued_urls == []


@pytest.mark.anyio
async def test_another_website_can_be_added_while_one_is_being_scanned(
    first_scan_in_progress: asyncio.Event, session: Session
) -> None:
    """Tests a second website can be added while the first is still having its first scan: it waits its turn
    rather than being refused, and each can be cancelled on its own."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={API_TOKEN_HEADER: config.api_token}
    ) as client:
        adding_first = asyncio.create_task(client.post("/scanner/initial_scan", json={"url": "https://first.com"}))
        await first_scan_in_progress.wait()
        adding_second = asyncio.create_task(client.post("/scanner/initial_scan", json={"url": "https://second.com"}))

        async with asyncio.timeout(5):  # Fails rather than hangs if the second website was not queued
            while len(scan_queue.queued_urls) < 2:
                await asyncio.sleep(0.01)

        first_url, second_url = scan_queue.queued_urls  # In the order they were added
        assert {website.url for website in session.scalars(select(DBWebsite))} == {first_url, second_url}

        # Cancelling the waiting website leaves the one being scanned alone
        assert (await client.post("/scanner/cancel", data={"url": second_url})).json() is True
        async with asyncio.timeout(5):
            assert (await adding_second).status_code == 409
        assert scan_queue.queued_urls == [first_url]

        assert (await client.post("/scanner/cancel", data={"url": first_url})).json() is True
        async with asyncio.timeout(5):
            assert (await adding_first).status_code == 409

    assert session.scalars(select(DBWebsite)).all() == []
    assert scan_queue.queued_urls == []


def test_adding_an_email_to_a_website_counts_as_emailing_it(
    api_client: TestClient,
    session: Session,
    test_website: website_models.WebsiteRead,
) -> None:
    """Tests an address added to a website is recorded as emailed (it was sent a confirmation), so its health
    checks count from then rather than never starting."""
    response = api_client.patch(f"/websites/{test_website.id}", json={"add_recipient_emails": ["new@example.com"]})

    assert response.status_code == 200, response.text
    recipient = session.scalars(select(DBRecipient).where(DBRecipient.email == "new@example.com")).one()
    assert recipient.last_email_at is not None


@pytest.mark.parametrize(
    "typed_url",
    [
        "/test_critical_page/",
        "https://www.test_website.com/test_critical_page",
        "https://TEST_WEBSITE.com/test_critical_page",
    ],
)
def test_adding_a_critical_page_already_watched_is_refused(
    api_client: TestClient,
    session: Session,
    test_critical_page: critical_page_models.CriticalPageRead,
    mocker: MockerFixture,
    typed_url: str,
) -> None:
    """Tests a critical page that is already watched is refused, including when it is written differently (a
    trailing slash, no "www." or capitals in the domain), so the same page is not watched (and reported) twice."""
    mock_check_pages_exist = mocker.patch("app.backend.websites.website_setup.check_pages_exist")

    response = api_client.post(
        "/scanner/initial_critical_page_scan",
        json={"website_id": str(test_critical_page.website_id), "url": typed_url},
    )

    assert response.status_code == 409, response.text
    assert "already being watched" in response.json()["detail"]
    mock_check_pages_exist.assert_not_called()
    pages = session.scalars(select(DBCriticalPage).where(DBCriticalPage.website_id == test_critical_page.website_id))
    assert [page.url for page in pages] == [str(test_critical_page.url)]


def test_adding_a_known_email_is_emailed_without_waiting_for_a_bounce(
    api_client: TestClient,
    session: Session,
    test_website: website_models.WebsiteRead,
    mocker: MockerFixture,
) -> None:
    """Tests an address that is already a recipient is only emailed, while a new address is also
    checked for a bounce.
    """
    session.add(DBRecipient(email="known@example.com"))
    session.flush()
    mock_confirm = mocker.patch("app.backend.websites.recipient_checks.confirm_address_can_receive_email")
    mock_send = mocker.patch("app.backend.websites.recipient_checks.send_confirmation")

    response = api_client.patch(
        f"/websites/{test_website.id}", json={"add_recipient_emails": ["known@example.com", "new@example.com"]}
    )

    assert response.status_code == 200, response.text
    mock_send.assert_called_once()
    assert mock_send.call_args.args[0].to == "known@example.com"
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args.args[0].to == "new@example.com"


def test_a_critical_pages_ignore_rules_can_be_set(
    api_client: TestClient, test_critical_page: critical_page_models.CriticalPageRead
) -> None:
    """Tests ignore rules are saved through the API, so text that changes every scan can be silenced."""
    response = api_client.patch(
        f"/critical_pages/{test_critical_page.id}", json={"ignore_rules": [r"Page last updated: .*"]}
    )

    assert response.status_code == 200, response.text
    assert api_client.get(f"/critical_pages/{test_critical_page.id}").json()["ignore_rules"] == [
        r"Page last updated: .*"
    ]


def test_an_invalid_ignore_rule_is_refused(
    api_client: TestClient, test_critical_page: critical_page_models.CriticalPageRead
) -> None:
    """Tests an ignore rule that is not a valid regular expression is refused with a 422 saying why."""
    response = api_client.patch(f"/critical_pages/{test_critical_page.id}", json={"ignore_rules": ["Fee is ($50"]})

    assert response.status_code == 422
    assert "valid regular expression" in response.text


# ==========================
#  Adding websites: what is refused
# ==========================


def test_adding_a_website_that_cannot_be_loaded_is_refused(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests a website whose page cannot be loaded is not added, and the wizard is told why."""

    def missing_page_client() -> AsyncClient:
        return AsyncClient(transport=MockTransport(lambda request: Response(404, text="Not Found")))

    mocker.patch("app.backend.websites.website_setup.new_http_client", side_effect=missing_page_client)

    response = api_client.post("/scanner/initial_scan", json={"url": "https://example.com/missing"})

    assert response.status_code == 422, response.text
    assert "https://example.com/missing could not be loaded" in response.json()["detail"]
    assert session.scalars(select(DBWebsite)).all() == []


def test_adding_an_address_that_cannot_receive_email_is_refused(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests a website is not added when one of its recipients' addresses bounces, and the wizard is told which."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch(
        "app.backend.websites.recipient_checks.confirm_address_can_receive_email",
        side_effect=UndeliverableEmailError("bounces@example.com"),
    )

    response = api_client.post(
        "/scanner/initial_scan", json={"url": "https://example.com", "recipient_emails": ["bounces@example.com"]}
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "We can't send an email to bounces@example.com. Check the address is correct."
    assert session.scalars(select(DBWebsite)).all() == []


@pytest.mark.parametrize("url", ["https://www.test_website.com/", "test_website.com", "https://TEST_WEBSITE.com"])
def test_adding_a_website_already_watched_is_refused(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture, url: str
) -> None:
    """Tests the same website cannot be added twice, even written differently, so it is not scanned and emailed
    twice."""
    mock_check_pages_exist = mocker.patch("app.backend.websites.website_setup.check_pages_exist")

    response = api_client.post("/scanner/initial_scan", json={"url": url})

    assert response.status_code == 409, response.text
    assert "is already being watched" in response.json()["detail"]
    mock_check_pages_exist.assert_not_called()


def test_a_different_part_of_a_watched_website_can_be_added(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests a section of a website that is already watched (e.g. example.com/research) can be added on its own."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch("app.backend.websites.website_setup.scan_website", return_value=make_scan_run())

    response = api_client.post("/scanner/initial_scan", json={"url": f"{test_website.url}research"})

    assert response.status_code == 200, response.text
    assert len(session.scalars(select(DBWebsite)).all()) == 2


def test_manual_scan_of_a_website_that_is_not_monitored_is_refused(api_client: TestClient, session: Session) -> None:
    """Tests "Run Scan Now" does not add a website it is given that is not monitored, as websites are only added
    through the wizard, which checks them."""
    response = api_client.post("/scanner/run", data={"url": "https://not-monitored.example.com"})

    assert response.status_code == 404, response.text
    assert session.scalars(select(DBWebsite)).all() == []


def test_manual_scan_emails_the_extra_address_even_when_the_website_has_no_recipients(
    api_client: TestClient, session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests "Run Scan Now" returns the report and emails the address it was given, even for a website with no
    recipients of its own."""
    session.add(DBWebsite(url="https://dashboard-only.example.com/"))
    session.flush()
    website = session.scalars(select(DBWebsite)).one()
    scan_run = _scan_finding_a_new_page(session, website_models.WebsiteRead.model_validate(website))
    mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=scan_run)

    response = api_client.post("/scanner/run", data={"url": website.url, "recipient_email": "someone@example.com"})

    assert response.status_code == 200, response.text
    assert "new-page" in response.json()
    assert [email.to for email in email_sender.sent] == ["someone@example.com"]


def test_adding_a_website_with_the_same_email_twice_adds_it_once(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests typing the same email twice in the wizard adds the website with one recipient, rather than failing with
    a half-added website."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist")  # The website is not loaded online
    mocker.patch("app.backend.websites.website_setup.scan_website", return_value=make_scan_run())

    response = api_client.post(
        "/scanner/initial_scan",
        json={"url": "https://example.com", "recipient_emails": ["jj@example.com", "jj@example.com"]},
    )

    assert response.status_code == 200, response.text
    website = session.scalars(select(DBWebsite)).one()
    assert [recipient.email for recipient in website.recipients] == ["jj@example.com"]


def test_a_recipient_time_without_a_time_zone_is_refused(
    api_client: TestClient, test_recipient: recipient_models.RecipientRead
) -> None:
    """Tests a time without a time zone is refused with a 422, as it cannot be saved as UTC."""
    response = api_client.patch(f"/recipients/{test_recipient.id}", json={"last_email_at": "2026-10-08T10:00:00"})

    assert response.status_code == 422, response.text
