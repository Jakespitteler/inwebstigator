from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.backend.scanning.health_checks import send_due_health_checks
from app.db.schema import DBRecipient, DBWebsite
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from tests.fakes import FakeEmailSender

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
async def test_health_checks_go_to_every_recipient(
    session: Session, mocker: MockerFixture, email_sender: FakeEmailSender
) -> None:
    """Tests the "no changes" health check considers every recipient, not just the first 100."""
    long_ago = datetime.now(UTC) - timedelta(days=30)
    emails = [f"person{number:03}@example.com" for number in range(MORE_THAN_A_PAGE)]
    # Health checks only go to recipients still linked to a website
    session.add(
        DBWebsite(
            url="https://example.com",
            recipients=[DBRecipient(email=email, last_email_at=long_ago) for email in emails],
        )
    )
    session.flush()
    mocker.patch("app.backend.scanning.health_checks.db_context", side_effect=lambda: nullcontext(session))
    mock_send_notification = mocker.patch("app.backend.scanning.notifications.send_notification")

    await send_due_health_checks(email_sender)

    checked_emails = {call.args[0].to for call in mock_send_notification.call_args_list}
    assert checked_emails == set(emails)
