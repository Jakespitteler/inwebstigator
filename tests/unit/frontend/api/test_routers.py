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
from app.frontend.api.utils import website_name
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
