"""Tests of the factory that makes the standard routes (list, get, create, update and delete) for a table.

The app's own routers leave out creating, updating and deleting, so these tests make a router with every route.
"""

from collections.abc import Iterator, Set

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.services.crud_protocol import CRUDOperation
from app.db.services.recipient_service import RecipientService
from app.db.session import get_db_session
from app.frontend.api.db_router_factory import create_crud_router
from app.main import ERROR_STATUS_CODES, error_response
from app.models.recipient_models import RecipientCreate, RecipientUpdate


def _client_for_router(session: Session, exclude: Set[CRUDOperation] = frozenset()) -> TestClient:
    """Makes an app with only a recipients router from the factory, using the test's database session.

    Args:
        session: The test's database session.
        exclude: The routes to leave out.

    Returns:
        A client for the app.
    """
    test_app = FastAPI()
    test_app.include_router(
        create_crud_router(
            prefix="/recipients",
            service_class=RecipientService,
            create_class=RecipientCreate,
            update_class=RecipientUpdate,
            exclude=exclude,
        )
    )
    test_app.dependency_overrides[get_db_session] = lambda: session
    for error_type, status_code in ERROR_STATUS_CODES.items():
        test_app.add_exception_handler(error_type, error_response(status_code))
    return TestClient(test_app)


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """Makes a client for an app with every route of a recipients router.

    Yields:
        The client.
    """
    with _client_for_router(session) as test_client:
        yield test_client


def test_a_record_is_created_read_updated_and_deleted(client: TestClient) -> None:
    """Tests each standard route does its operation, answering with the record as it is saved."""
    created = client.post("/recipients/", json={"email": "jj@example.com"})
    assert created.status_code == 201, created.text
    recipient_id: str = created.json()["id"]

    assert client.get(f"/recipients/{recipient_id}").json()["email"] == "jj@example.com"
    assert [recipient["id"] for recipient in client.get("/recipients/").json()] == [recipient_id]

    updated = client.patch(f"/recipients/{recipient_id}", json={"days_between_health_checks": 3})
    assert updated.status_code == 200, updated.text
    assert updated.json()["days_between_health_checks"] == 3

    assert client.delete(f"/recipients/{recipient_id}").status_code == 204
    assert client.get(f"/recipients/{recipient_id}").status_code == 404


def test_a_record_that_does_not_exist_is_not_found(client: TestClient) -> None:
    """Tests reading, updating or deleting a record that does not exist answers with a 404 status."""
    missing_id: str = "00000000-0000-0000-0000-000000000000"

    assert client.get(f"/recipients/{missing_id}").status_code == 404
    assert client.patch(f"/recipients/{missing_id}", json={"days_between_health_checks": 3}).status_code == 404
    assert client.delete(f"/recipients/{missing_id}").status_code == 404


def test_a_record_that_is_not_valid_is_refused(client: TestClient) -> None:
    """Tests a record sent to the create route is checked against the create model before anything is saved."""
    response = client.post("/recipients/", json={"email": "not an email address"})

    assert response.status_code == 422
    assert client.get("/recipients/").json() == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/recipients/"), ("patch", "/recipients/00000000-0000-0000-0000-000000000000")],
    ids=["create", "update"],
)
def test_left_out_operations_have_no_route(session: Session, method: str, path: str) -> None:
    """Tests the operations left out are not routed, so a router can replace them with routes of its own."""
    with _client_for_router(session, exclude={CRUDOperation.CREATE, CRUDOperation.UPDATE}) as client:
        response = client.request(method, path, json={})

    assert response.status_code == 405
