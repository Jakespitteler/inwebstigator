from contextlib import nullcontext

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app import scanner
from app.db.schema import DBRecipient, DBWebsite
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.scheduler import _scan_then_send_health_checks  # pyright: ignore[reportPrivateUsage]

# More than the API's default page size of 100, which the dashboard, scheduled scans and
# health checks used to stop at without saying so
MORE_THAN_A_PAGE: int = 105


def _add_websites(session: Session, count: int) -> list[str]:
    urls = [f"https://site{number:03}.example.com" for number in range(count)]
    session.add_all(DBWebsite(url=url) for url in urls)
    session.flush()
    return urls


def _add_recipients(session: Session, count: int) -> list[str]:
    emails = [f"person{number:03}@example.com" for number in range(count)]
    session.add_all(DBRecipient(email=email) for email in emails)
    session.flush()
    return emails


def test_api_still_pages_websites_and_recipients_by_100(session: Session) -> None:
    """Tests the API's list endpoints keep their page size of 100, while `limit=None` returns everything."""
    _add_websites(session, MORE_THAN_A_PAGE)
    _add_recipients(session, MORE_THAN_A_PAGE)

    assert len(WebsiteService(session).get_all()) == 100
    assert len(WebsiteService(session).get_all(limit=None)) == MORE_THAN_A_PAGE
    assert len(RecipientService(session).get_all()) == 100
    assert len(RecipientService(session).get_all(limit=None)) == MORE_THAN_A_PAGE


def test_dashboard_shows_every_website(api_client: TestClient, session: Session) -> None:
    """Tests the dashboard lists every monitored website, not just the first 100."""
    urls = _add_websites(session, MORE_THAN_A_PAGE)

    response = api_client.get("/")
    assert response.status_code == 200, response.text

    page = BeautifulSoup(response.text, "html.parser")
    assert len(page.select("#websites-panel .website-card")) == MORE_THAN_A_PAGE
    assert urls[-1] in response.text


@pytest.mark.anyio
async def test_scheduled_scan_checks_every_website(session: Session, mocker: MockerFixture) -> None:
    """Tests a scheduled scan goes through every monitored website, not just the first 100."""
    urls = _add_websites(session, MORE_THAN_A_PAGE)
    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mock_scan_website = mocker.patch("app.scanner.scan_website", return_value=None)

    await scanner.scan_all_websites()

    scanned_urls = {call.args[1].url for call in mock_scan_website.call_args_list}
    assert scanned_urls == set(urls)


@pytest.mark.anyio
async def test_health_checks_go_to_every_recipient(session: Session, mocker: MockerFixture) -> None:
    """Tests the "no changes" health check considers every recipient, not just the first 100."""
    emails = _add_recipients(session, MORE_THAN_A_PAGE)
    mocker.patch("app.scheduler.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.scheduler.scan_all_websites", return_value=None)
    mock_health_check = mocker.patch("app.scheduler._send_health_check_if_no_change")

    await _scan_then_send_health_checks()

    checked_emails = {call.args[0].email for call in mock_health_check.call_args_list}
    assert checked_emails == set(emails)
