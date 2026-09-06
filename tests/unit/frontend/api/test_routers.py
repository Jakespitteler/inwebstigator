import uuid

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from app.db.core import Base
from app.db.models import critical_page_models, internal_link_models, website_models
from app.frontend.api import routers


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


class TestWebsiteRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.WEBSITE_ROUTER.prefix
    model_create = website_models.WebsiteCreate(
        url="https://www.test_website.com", critical_pages=[], internal_links=[]
    )
    model_update = website_models.WebsiteUpdate(url="https://www.updated_website.com")
    fixture_name = "test_website"


class TestCriticalPageRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.CRITICAL_PAGE_ROUTER.prefix
    model_create = critical_page_models.CriticalPageCreate(
        url="https://www.test_website.com/test_critical_page",
        links=[],
        documents=[],
        text_body="",
        website_id=uuid.uuid4(),
    )
    model_update = critical_page_models.CriticalPageUpdate(url="https://www.test_website.com/updated_critical_page")
    fixture_name = "test_critical_page"


class TestInternalLinkRouter(TestCRUDRouters):
    __test__ = True
    prefix = routers.INTERNAL_LINK_ROUTER.prefix
    model_create = internal_link_models.InternalLinkCreate(
        url="https://www.test_website.com/test_internal_link",
        website_id=uuid.uuid4(),
    )
    model_update = internal_link_models.InternalLinkUpdate(url="https://www.test_website.com/updated_internal_link")
    fixture_name = "test_internal_link"

    def test_create_batch_internal_links(
        self,
        api_client: TestClient,
        test_website: website_models.WebsiteRead,
    ) -> None:
        """
        Tests creating multiple internal links in batch via the router endpoint.

        Args:
            api_client: The FastAPI test client.
            test_website: An existing website fixture for the foreign key.
        """
        batch_payload = internal_link_models.InternalLinkCreateBatch(
            urls=[
                "https://www.test_website.com/batch_link_1",
                "https://www.test_website.com/batch_link_2",
            ],
            website_id=test_website.id,
        )

        response = api_client.post(
            url=f"{self.prefix}/batch",
            json=batch_payload.model_dump(mode="json"),
        )

        assert response.status_code == 201, response.text
        data = response.json()
        assert len(data) == 2
        assert data[0]["url"] == "https://www.test_website.com/batch_link_1"
        assert data[1]["url"] == "https://www.test_website.com/batch_link_2"
        assert data[0]["website_id"] == str(test_website.id)
