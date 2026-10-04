import uuid
from contextlib import nullcontext
from datetime import datetime

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.db.schema import Base, DBWebsite
from app.frontend.api import routers
from app.models import critical_page_models, recipient_models, website_models


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


def _latest_scan_text(api_client: TestClient) -> str | None:
    """Returns the dashboard's "Latest scan" line, or None if it is not shown."""
    response = api_client.get("/")
    assert response.status_code == 200, response.text
    line = BeautifulSoup(response.text, "html.parser").select_one(".week-range")
    return " ".join(line.get_text().split()) if line else None


def test_dashboard_shows_most_recent_scan_across_websites(api_client: TestClient, session: Session) -> None:
    """Tests the dashboard shows when the most recently scanned website was last scanned."""
    session.add_all(
        [
            DBWebsite(url="https://older.example.com", last_scan_at=datetime(2026, 9, 28, 9, 5)),
            DBWebsite(url="https://newer.example.com", last_scan_at=datetime(2026, 10, 1, 14, 30)),
            DBWebsite(url="https://never-scanned.example.com"),
        ]
    )
    session.flush()

    assert _latest_scan_text(api_client) == "Latest scan: 01 Oct 2026, 14:30"


def test_dashboard_hides_latest_scan_when_nothing_scanned(api_client: TestClient, session: Session) -> None:
    """Tests the "Latest scan" line is left out until a website has been scanned."""
    session.add(DBWebsite(url="https://never-scanned.example.com"))
    session.flush()

    assert _latest_scan_text(api_client) is None


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
    assert _latest_scan_text(api_client) == f"Latest scan: {last_scan_at:%d %b %Y, %H:%M}"


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
