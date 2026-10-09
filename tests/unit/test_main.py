import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import config
from app.core.errors import WebConnectionError
from app.core.logging_setup import WEB_SERVER_LOGGER, setup_logging
from app.main import app
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteRead


def test_starting_the_app_sets_up_logging_and_the_database(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests logging and the database are set up when the app starts, not when its code is imported, and that the
    scheduled scans are left off when automatic scans are turned off."""
    monkeypatch.setattr(config, "automatic_scans", False)
    mock_setup_logging = mocker.patch("app.main.setup_logging")
    mock_prepare_database = mocker.patch("app.main.prepare_database")
    mock_schedule_scans = mocker.patch("app.main.schedule_scans")

    with TestClient(app):
        mock_setup_logging.assert_called_once_with(
            config.log_path, config.log_file_max_bytes, config.log_file_backup_count
        )
        mock_prepare_database.assert_called_once()
        mock_schedule_scans.assert_not_called()


def test_setup_logging_also_writes_to_a_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the logs are written to a file in a folder it creates, so testers' copies of the app keep a record."""
    root_logger = logging.getLogger()
    web_server_logger = logging.getLogger(WEB_SERVER_LOGGER)
    monkeypatch.setattr(root_logger, "handlers", [])
    monkeypatch.setattr(root_logger, "level", root_logger.level)
    monkeypatch.setattr(web_server_logger, "handlers", [])
    log_path = tmp_path / "logs" / "inwebstigator.log"

    setup_logging(log_path, max_bytes=1_000, backup_count=2)
    logging.getLogger("app.test").warning("Something to look into")
    web_server_logger.error("The server could not start")
    for handler in root_logger.handlers:
        handler.close()

    log_text: str = log_path.read_text(encoding="utf-8")
    assert "Something to look into" in log_text
    assert "The server could not start" in log_text


def test_automatic_scans_run_the_scheduler_while_the_app_is_running(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests the scheduled scans start when the app starts and stop when it closes, when automatic scans are on."""
    monkeypatch.setattr(config, "automatic_scans", True)
    scheduler_events: list[str] = []

    @asynccontextmanager
    async def record_scheduler(scheduled_app: FastAPI) -> AsyncGenerator[None]:
        scheduler_events.append("started")
        yield
        scheduler_events.append("stopped")

    mocker.patch("app.main.schedule_scans", side_effect=record_scheduler)

    with TestClient(app):
        assert scheduler_events == ["started"]
    assert scheduler_events == ["started", "stopped"]


# ==========================
#  Error responses
# ==========================


def test_a_record_that_does_not_exist_is_a_404_naming_it(api_client: TestClient) -> None:
    """Tests asking for a record that does not exist fails with a 404 whose message names the missing ID."""
    missing_id = uuid.uuid4()

    response = api_client.get(f"/websites/{missing_id}")

    assert response.status_code == 404, response.text
    assert str(missing_id) in response.json()["detail"]
    assert "not found" in response.json()["detail"]


def test_a_record_breaking_a_database_rule_is_a_400(api_client: TestClient, test_recipient: RecipientRead) -> None:
    """Tests adding a record the database refuses (here a second recipient with the same email) fails with a 400
    saying what to check, rather than a server error."""
    response = api_client.post("/recipients", json={"email": test_recipient.email})

    assert response.status_code == 400, response.text
    assert "unique constraints" in response.json()["detail"]


def test_a_website_that_cannot_be_reached_is_a_502(
    api_client: TestClient, test_website: WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests a connection problem reaching a website fails with a 502 (a problem with the other website, not the
    app), saying which website could not be reached."""
    mocker.patch("app.frontend.api.routers.scan_website_now", side_effect=WebConnectionError(str(test_website.url)))

    response = api_client.post("/scanner/run", data={"url": str(test_website.url)})

    assert response.status_code == 502, response.text
    assert str(test_website.url) in response.json()["detail"]


def test_a_critical_page_on_another_website_is_a_422(api_client: TestClient, test_website: WebsiteRead) -> None:
    """Tests adding a critical page that is on a different website fails with a 422 saying why."""
    response = api_client.post(
        "/scanner/initial_critical_page_scan",
        json={"website_id": str(test_website.id), "url": "https://other.example.com/news"},
    )

    assert response.status_code == 422, response.text
    assert "is not a page on" in response.json()["detail"]
