import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import config
from app.frontend.api.request_guard import API_TOKEN_HEADER, is_allowed_host


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1:48731", True),
        ("localhost:8000", True),
        ("LOCALHOST", True),
        ("attacker.example.com", False),
        ("attacker.example.com:48731", False),
        ("", False),
    ],
)
def test_only_this_computers_host_names_are_allowed(host: str, allowed: bool) -> None:
    """Tests requests are only accepted for this computer's own host names, whatever the port."""
    assert is_allowed_host(host, ["127.0.0.1", "localhost"]) is allowed


@pytest.mark.parametrize("headers", [{}, {API_TOKEN_HEADER: "a-guess"}], ids=["no-token", "wrong-token"])
def test_a_request_that_changes_something_needs_the_dashboards_token(
    api_client: TestClient, headers: dict[str, str]
) -> None:
    """Tests a request from a page on another website, which cannot read the dashboard's token, cannot start a scan."""
    api_client.headers.pop(API_TOKEN_HEADER)

    response = api_client.post("/scanner/run_all", headers=headers)

    assert response.status_code == 403, response.text


def test_reading_the_dashboard_needs_no_token(api_client: TestClient) -> None:
    """Tests the dashboard page itself loads without a token, and gives its scripts the token to send."""
    api_client.headers.pop(API_TOKEN_HEADER)

    response = api_client.get("/")

    assert response.status_code == 200, response.text
    token_tag = BeautifulSoup(response.text, "html.parser").select_one('meta[name="api-token"]')
    assert token_tag is not None and token_tag["content"] == config.api_token


def test_a_request_for_another_host_is_refused(api_client: TestClient) -> None:
    """Tests a request naming another website's host (as in DNS rebinding) is refused, even with the token."""
    response = api_client.get("/", headers={"Host": "attacker.example.com"})

    assert response.status_code == 400, response.text


def test_requests_are_let_through_when_the_token_is_turned_off(api_client: TestClient, mocker: MockerFixture) -> None:
    """Tests that with `API_TOKEN_REQUIRED=false` (for development) a request without the token, for any host, is
    handled."""
    mocker.patch.object(config, "api_token_required", False)
    api_client.headers.pop(API_TOKEN_HEADER)

    response = api_client.post(
        "/scanner/cancel", data={"url": "https://not-queued.example.com"}, headers={"Host": "attacker.example.com"}
    )

    assert response.status_code == 200, response.text
    assert response.json() is False
