import asyncio
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient, MockTransport, Response
from pydantic import BaseModel
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import config
from app.core.errors import ScanCancelledError, WebsiteTooLargeError
from app.db.core import get_db_session
from app.db.schema import Base, DBCriticalPage, DBRecipient, DBWebsite
from app.frontend.api import routers
from app.frontend.api.utils import website_name
from app.main import app
from app.models import critical_page_models, recipient_models, website_models
from app.scanner import queued_crawls


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

    @pytest.fixture
    def api_record(self, request: pytest.FixtureRequest) -> Base:
        """Dynamically fetches the database record fixture required by the subclass."""
        return request.getfixturevalue(self.fixture_name)

    def test_get_all_records(self, api_client: TestClient) -> None:
        """
        Tests retrieving a list of records from an api router.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.get(url=self.prefix)
        assert response.status_code == 200, response.text

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

    def test_get_record_not_found(self, api_client: TestClient) -> None:
        """
        Tests that retrieving a non-existent record returns a 404 status.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.get(url=f"{self.prefix}/{uuid.uuid4()}")
        assert response.status_code == 404, response.text

    def test_create_record(self, api_client: TestClient) -> None:
        """
        Tests creating a new record.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
        """
        response: Response = api_client.post(
            url=self.prefix,
            json=self.model_create.model_dump(mode="json"),
        )
        assert response.status_code == 201, response.text

    def test_update_record(self, api_client: TestClient, api_record: Base) -> None:
        """
        Tests updating an existing record's details.

        Args:
            api_client: The FastAPI test client.
            router_test_config: The router configuration.
            test_api_record: An existing record.
        """
        response: Response = api_client.patch(
            url=f"{self.prefix}/{api_record.id}",
            json=self.model_update.model_dump(exclude_unset=True),
        )
        assert response.status_code == 200, response.text

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
    model_create = website_models.WebsiteCreate(url="https://www.test_website.com")
    model_update = website_models.WebsiteUpdate(url="https://www.updated_website.com")
    fixture_name = "test_website"


class TestCriticalPageRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.CRITICAL_PAGE_ROUTER.prefix
    model_create = critical_page_models.CriticalPageCreate(
        url="https://www.test_website.com/test_critical_page",
        website_id=uuid.uuid4(),
    )
    model_update = critical_page_models.CriticalPageUpdate(links=["https://www.test_website.com/updated_critical_page"])
    fixture_name = "test_critical_page"


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
    """Returns each website card's "Last scanned" line, keyed by the website's display name."""
    response = api_client.get("/")
    assert response.status_code == 200, response.text
    cards = BeautifulSoup(response.text, "html.parser").select(".website-card")
    return {
        card["data-website-name"]: " ".join(card.select_one(".last-scan").get_text().split())  # type: ignore[union-attr]
        for card in cards
    }


def test_dashboard_shows_each_websites_own_scan_time(api_client: TestClient, session: Session) -> None:
    """Tests every website card shows when that website was last scanned, not the latest scan overall."""
    session.add_all(
        [
            DBWebsite(url="https://older.example.com", last_scan_at=datetime(2026, 9, 28, 9, 5)),
            DBWebsite(url="https://newer.example.com", last_scan_at=datetime(2026, 10, 1, 14, 30)),
            DBWebsite(url="https://never-scanned.example.com"),
        ]
    )
    session.flush()

    assert _last_scan_by_website(api_client) == {
        "older.example.com": "Last scanned: 28 Sep 2026, 09:05",
        "newer.example.com": "Last scanned: 01 Oct 2026, 14:30",
        "never-scanned.example.com": "Last scanned: Not scanned yet",
    }


def test_dashboard_shows_next_scheduled_check(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests the dashboard shows when the scheduler next checks which websites are due a scan."""
    mocker.patch("app.frontend.api.routers.next_scheduled_check", return_value=datetime(2026, 10, 5, 21, 30))

    assert _next_check_text(api_client) == "Next scheduled check: 05 Oct 2026, 21:30"


def test_dashboard_hides_next_check_when_scans_are_not_scheduled(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests the "Next scheduled check" line is left out when automatic scans are not running."""
    mocker.patch("app.frontend.api.routers.next_scheduled_check", return_value=None)

    assert _next_check_text(api_client) is None


def _changed_page(url: str, changed_at: datetime) -> DBCriticalPage:
    """Creates a critical page with a recent change that was found at the given time."""
    return DBCriticalPage(url=url, recent_links_added=[f"{url}/new-link"], last_changed_at=changed_at)


def test_updates_are_listed_most_recent_change_first(api_client: TestClient, session: Session) -> None:
    """Tests websites, and each website's critical pages, are listed most recent change first with when it was found."""
    session.add_all(
        [
            DBWebsite(
                url="https://older.example.com",
                critical_pages=[_changed_page("https://older.example.com", datetime(2026, 10, 1, 9, 0))],
            ),
            DBWebsite(
                url="https://newer.example.com",
                critical_pages=[
                    _changed_page("https://newer.example.com", datetime(2026, 10, 2, 9, 0)),
                    _changed_page("https://newer.example.com/news", datetime(2026, 10, 4, 9, 0)),
                ],
            ),
        ]
    )
    session.flush()

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    website_names = [name.get_text(strip=True) for name in dashboard.select(".website-change-record .website-name")]
    page_urls = [url.get_text(strip=True) for url in dashboard.select(".page-change-record .page-url")]
    change_times = [" ".join(time.get_text().split()) for time in dashboard.select(".page-change-record .change-time")]

    assert website_names == ["newer.example.com", "older.example.com"]
    assert page_urls == ["https://newer.example.com/news", "https://newer.example.com", "https://older.example.com"]
    assert change_times == ["Changed 04 Oct 2026, 09:00", "Changed 02 Oct 2026, 09:00", "Changed 01 Oct 2026, 09:00"]


def test_run_all_scans_every_website_and_restarts_the_countdown(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests "Run All Scans" restarts the countdown to the next scheduled check and scans every website, due or not."""
    mock_restart_scan_countdown = mocker.patch("app.frontend.api.routers.restart_scan_countdown")
    mock_scan_all_websites = mocker.patch("app.frontend.api.routers.scan_all_websites", return_value=None)

    response = api_client.post("/scanner/run_all")

    assert response.status_code == 200, response.text
    mock_restart_scan_countdown.assert_called_once()
    mock_scan_all_websites.assert_awaited_once_with(ignore_schedule=True)


def test_manual_scan_records_scan_time(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Run Scan Now" records when the website was scanned, so the dashboard and scheduler see it."""
    mocker.patch("app.frontend.api.routers.scan_website", return_value=None)
    mocker.patch("app.frontend.api.routers.db_context", side_effect=lambda: nullcontext(session))
    before = datetime.now()

    response = api_client.post("/scanner/run", data={"url": test_website.url})

    assert response.status_code == 200, response.text
    website = session.get(DBWebsite, test_website.id)
    assert website
    last_scan_at = website.last_scan_at
    assert last_scan_at is not None and last_scan_at >= before
    assert _last_scan_by_website(api_client)[website_name(test_website.url)] == (
        f"Last scanned: {last_scan_at:%d %b %Y, %H:%M}"
    )


def test_manual_scan_of_a_website_already_queued_is_refused(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Run Scan Now" for a website already queued or being scanned is refused rather than queued twice."""
    mocker.patch.dict("app.scanner.queued_crawls", {test_website.url: mocker.Mock()})
    mock_get_website_updates = mocker.patch("app.scanner.get_website_updates")

    response = api_client.post("/scanner/run", data={"url": test_website.url})

    assert response.status_code == 409, response.text
    mock_get_website_updates.assert_not_called()


def test_cancel_scan_reports_whether_there_was_a_scan_to_cancel(
    api_client: TestClient, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Cancel Scan" cancels a queued or running scan, and says so when there was nothing to cancel."""
    mock_crawl = mocker.Mock()
    mock_crawl.cancel.return_value = True
    mocker.patch.dict("app.scanner.queued_crawls", {test_website.url: mock_crawl})

    assert api_client.post("/scanner/cancel", data={"url": test_website.url}).json() is True
    mock_crawl.cancel.assert_called_once()
    assert api_client.post("/scanner/cancel", data={"url": "https://not-queued.com"}).json() is False


def test_dashboard_shows_cancel_button_only_for_websites_being_scanned(
    api_client: TestClient, session: Session, test_website: website_models.WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests a website that is queued or being scanned shows "Cancel Scan" instead of "Run Scan Now" after a
    refresh, and other websites do not."""
    session.add(DBWebsite(url="https://not-scanning.example.com"))
    session.flush()
    mocker.patch.dict("app.scanner.queued_crawls", {test_website.url: mocker.Mock()})

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")

    for url, scanning in [(test_website.url, True), ("https://not-scanning.example.com", False)]:
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
    mocker.patch.dict("app.scanner.queued_crawls", {test_website.url: mock_crawl})

    response = api_client.delete(f"/websites/{test_website.id}")

    assert response.status_code == 204, response.text
    mock_crawl.cancel.assert_called_once()
    assert session.get(DBWebsite, test_website.id) is None


def test_cancelling_the_first_scan_does_not_add_the_website(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests cancelling a new website's first scan cancels adding it, so no website is left without a baseline."""
    mocker.patch("app.frontend.api.routers.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers._check_pages_exist")  # The website is not loaded online
    mocker.patch("app.frontend.api.routers.scan_website", side_effect=ScanCancelledError("https://example.com"))
    mock_send_monitoring_started = mocker.patch("app.frontend.api.routers.send_monitoring_started_notifications")

    response = api_client.post("/scanner/initial_scan", json={"url": "https://example.com"})

    assert response.status_code == 409, response.text
    assert session.scalars(select(DBWebsite)).all() == []
    mock_send_monitoring_started.assert_not_called()


def test_adding_a_website_too_large_to_scan_deactivates_it(
    api_client: TestClient, session: Session, mocker: MockerFixture
) -> None:
    """Tests a new website too large to scan is kept but deactivated, its critical pages are still watched (so its
    recipients are told monitoring has started), and the dashboard says why it was deactivated."""
    main_url = "https://example.com"

    def mock_client() -> AsyncClient:
        return AsyncClient(transport=MockTransport(lambda request: Response(200, text="<p>Home page.</p>")))

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers.AsyncClient", side_effect=mock_client)
    mocker.patch("app.backend.engine.crawl_site", side_effect=WebsiteTooLargeError(main_url, max_pages=50_000))
    mock_send_monitoring_started = mocker.patch("app.frontend.api.routers.send_monitoring_started_notifications")

    response = api_client.post(
        "/scanner/initial_scan", json={"url": main_url, "recipient_emails": ["someone@example.com"]}
    )

    assert response.status_code == 200, response.text
    website = session.scalars(select(DBWebsite)).one()
    assert website.active is False
    assert website.deactivated_reason == website_models.DeactivationReason.TOO_LARGE
    assert website.critical_pages[0].text_body == "<p>Home page.</p>"  # The main page's baseline was saved
    mock_send_monitoring_started.assert_called_once()

    notice = _website_notice(api_client, main_url)
    assert notice is not None
    assert f"more than {config.web_crawler_default_max_pages:,} pages" in notice


def _website_notice(api_client: TestClient, url: str) -> str | None:
    """Returns the notice on a website's dashboard card saying why it was deactivated, or None if it has none."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    delete_button = dashboard.select_one(f'.delete-website-button[data-website-url="{url}"]')
    assert delete_button is not None
    card = delete_button.find_parent(class_="website-card")
    assert card is not None
    notice = card.select_one(".website-notice")
    return " ".join(notice.get_text().split()) if notice else None


def _run_scan_button_text(api_client: TestClient, url: str) -> str:
    """Returns the text of a website's run scan button on the dashboard."""
    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    button = dashboard.select_one(f'.run-scan-button[data-website-url="{url}"]')
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
        assert _run_scan_button_text(api_client, url) == "Scan Critical Pages Now"
    for url in ["https://active.example.com", "https://reactivated.example.com"]:
        assert _website_notice(api_client, url) is None
        assert _run_scan_button_text(api_client, url) == "Run Scan Now"


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
def first_scan_in_progress(mocker: MockerFixture, session: Session) -> Iterator[asyncio.Event]:
    """Makes a new website's first scan run until it is cancelled, and runs the app against the test database.

    Returns:
        An event that is set once the first scan has started crawling.
    """
    crawl_started = asyncio.Event()

    async def crawl_until_cancelled(*args: object) -> None:
        crawl_started.set()
        await asyncio.Event().wait()

    mocker.patch("app.scanner.scan_lock", asyncio.Lock())  # A lock for this test's event loop
    mocker.patch("app.frontend.api.routers._check_pages_exist")  # The website is not loaded online
    mocker.patch("app.scanner.get_website_updates", side_effect=crawl_until_cancelled)
    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers.db_context", side_effect=lambda: nullcontext(session))
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        adding = asyncio.create_task(client.post("/scanner/initial_scan", json={"url": url}))
        await first_scan_in_progress.wait()

        if cancel_by == "cancelling the scan":
            response = await client.post("/scanner/cancel", data={"url": url})
            assert response.json() is True
        else:
            website = session.scalars(select(DBWebsite).where(DBWebsite.url == url)).one()
            response = await client.delete(f"/websites/{website.id}")
            assert response.status_code == 204, response.text

        async with asyncio.timeout(5):  # Fails rather than hangs if the scan was not stopped
            added = await adding

    assert added.status_code == 409, added.text
    assert session.scalars(select(DBWebsite)).all() == []
    assert queued_crawls == {}


def test_adding_a_known_email_is_emailed_without_waiting_for_a_bounce(
    api_client: TestClient,
    session: Session,
    test_website: website_models.WebsiteRead,
    mocker: MockerFixture,
) -> None:
    """Tests an address that is already a recipient is only emailed, while a new address is also checked for a bounce."""
    session.add(DBRecipient(email="known@example.com"))
    session.flush()
    mock_confirm = mocker.patch("app.frontend.api.routers.confirm_address_can_receive_email")
    mock_send = mocker.patch("app.frontend.api.routers.send_confirmation")

    response = api_client.patch(
        f"/websites/{test_website.id}", json={"add_recipient_emails": ["known@example.com", "new@example.com"]}
    )

    assert response.status_code == 200, response.text
    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "known@example.com"
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args.args[0] == "new@example.com"

