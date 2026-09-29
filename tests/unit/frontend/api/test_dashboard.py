from fastapi.testclient import TestClient


def test_dashboard_loads(
    api_client: TestClient,
) -> None:
    """Tests that the dashboard can be accessed without authentication."""
    response = api_client.get("/")

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
            "days_between_scans": 2,
        },
    )

    assert response.status_code == 200

    updated_website = response.json()

    assert updated_website["active"] is False
    assert updated_website["recommended_delay"] == 2.5
    assert updated_website["recommended_concurrent"] == 4
    assert updated_website["days_between_scans"] == 2


def test_dashboard_runs_manual_scan(
    api_client: TestClient,
    test_website,
    session,
    mocker,
) -> None:
    """Tests manually running a website scan from the dashboard."""
    mock_scan_website = mocker.patch(
        "app.frontend.api.routers.scan_website",
        return_value="<p>Website Updated</p>",
    )

    mocker.patch.object(session, "commit")

    response = api_client.post(
        "/scanner/run",
        data={"url": test_website.url},
    )

    assert response.status_code == 200
    assert response.json() == "<p>Website Updated</p>"
    mock_scan_website.assert_awaited_once()