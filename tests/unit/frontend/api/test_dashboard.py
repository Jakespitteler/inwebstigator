from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import config


def test_dashboard_redirects_to_login_when_logged_out(
    api_client: TestClient,
) -> None:
    """Tests that logged-out users cannot access the dashboard."""
    config.user_id = None

    response = api_client.get(
        "/dashboard",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_loads_when_logged_in(
    api_client: TestClient,
    test_user,
    session: Session,
    monkeypatch,
) -> None:
    """Tests that a logged-in user can access the dashboard."""
    config.user_id = test_user.id

    monkeypatch.setattr(
        "app.main.SessionLocal",
        lambda: session,
    )

    response = api_client.get("/dashboard")

    assert response.status_code == 200

def test_dashboard_updates_website_scan_settings(
    api_client: TestClient,
    test_website,
) -> None:
    """Tests updating website scan settings used by the dashboard."""
    response = api_client.patch(
        f"/websites/{test_website.id}",
        json={
            "active": False,
            "recommended_delay": 2.5,
            "recommended_concurrent": 4,
        },
    )

    assert response.status_code == 200

    updated_website = response.json()

    assert updated_website["active"] is False
    assert updated_website["recommended_delay"] == 2.5
    assert updated_website["recommended_concurrent"] == 4


def test_dashboard_updates_schedule_settings(
    api_client: TestClient,
    test_user,
) -> None:
    """Tests updating user schedule settings used by the dashboard."""
    response = api_client.patch(
        f"/users/{test_user.id}",
        json={
            "days_between_scans": 2,
            "days_between_heath_checks": 5,
        },
    )

    assert response.status_code == 200

    updated_user = response.json()

    assert updated_user["days_between_scans"] == 2
    assert updated_user["days_between_heath_checks"] == 5