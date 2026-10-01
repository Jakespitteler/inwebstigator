from contextlib import nullcontext
from datetime import datetime, timedelta
from email.message import EmailMessage

import httpx2
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app import scanner
from app.core.errors import TrafficError, WebConnectionError
from app.db import repository
from app.db.schema import DBWebsite
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteCreate, WebsiteRead

# ======================================
# Setup Fixtures
# ======================================


@pytest.fixture
def populated_website(
    session: Session,
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,
    test_recipient: RecipientRead,
) -> WebsiteRead:
    """Provides a WebsiteRead model fully populated with its internal relationships and recipients."""
    return test_website.model_copy(
        update={
            "internal_links": [test_internal_link],
            "critical_pages": WebsiteService(session).ensure_main_critical_page(test_website.id).critical_pages,
            "recipients": [test_recipient],
        }
    )


# ======================================
# send_notification Tests
# ======================================


def test_send_notification_success(mocker: MockerFixture, test_recipient: RecipientRead):
    """Tests that send_notification builds and sends an email, updating recipient.last_email_at."""
    mock_build_message = mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")
    mock_recipient_service = mocker.patch.object(RecipientService, "get_by_email")
    mock_recipient_service.return_value = test_recipient
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    mock_msg = EmailMessage()
    mock_build_message.return_value = mock_msg

    result = scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>")

    mock_build_message.assert_called_once_with(
        subject="Website Update",
        recipients=[test_recipient.email],
        html_body="<p>Scan Report HTML</p>",
    )
    mock_send_email.assert_called_once_with(mock_msg)
    mock_recipient_service_update.assert_called_once()
    assert result is None


def test_send_notification_failure(mocker: MockerFixture, test_recipient: RecipientRead):
    """Tests that send_notification propagates exceptions if email delivery fails, preventing metadata updates."""
    mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")
    mock_send_email.side_effect = Exception("SMTP connection timed out")
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    with pytest.raises(Exception, match="SMTP connection timed out"):
        scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>")

    mock_send_email.assert_called_once()
    mock_recipient_service_update.assert_not_called()


# ======================================
# scan_website Tests
# ======================================


@pytest.mark.anyio
@pytest.mark.parametrize("existing_website", [False, True], ids=["new-website", "existing-website"])
async def test_main_url_content_is_scanned_and_shown_in_updates(
    session: Session, mocker: MockerFixture, api_client: TestClient, existing_website: bool
):
    """Scan the starting page without manually adding it, then detect a real content change."""
    main_url = "https://example.com/au?edition=local"
    service = WebsiteService(session)
    if existing_website:
        record = DBWebsite(url=main_url)
        repository.add(session, record)
        website = WebsiteRead.model_validate(record)
        assert website.critical_pages == []
    else:
        website = service.create(WebsiteCreate(url=main_url))

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.engine.crawl_site", return_value=set())
    requested_urls = []
    html = "<html><body><p>Original main page content.</p></body></html>"

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await scanner.scan_website(client, website)
        baseline = service.get(website.id)
        assert len(baseline.critical_pages) == 1
        main_page = baseline.critical_pages[0]
        assert main_page.url == main_url
        assert main_page.text_body == html

        html = html.replace("</body>", "<p>New main-page announcement.</p></body>")
        report = await scanner.scan_website(client, baseline)
        updated = service.get(website.id)
        assert updated.critical_pages[0].id == main_page.id
        assert updated.critical_pages[0].text_body == html
        assert "New main-page announcement." in report

        response = api_client.get("/")
        assert response.status_code == 200
        dashboard = BeautifulSoup(response.text, "html.parser")
        assert "New main-page announcement." in dashboard.select_one("#updates-panel").get_text()
        assert "Main website (automatic)" in dashboard.select_one("#websites-panel").get_text()

        assert await scanner.scan_website(client, updated) is None

    assert requested_urls == [main_url, main_url, main_url]
    assert len(service.get(website.id).critical_pages) == 1


@pytest.mark.anyio
async def test_scan_website_traffic_error_handling(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that TrafficError triggers cooldown handling and returns a traffic error HTML report."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanner.get_website_updates",
        side_effect=TrafficError(url=populated_website.url, status_code=429),
    )
    mock_handle_traffic = mocker.patch.object(WebsiteService, "handle_traffic_error", return_value="Cooldown applied")

    report = await scanner.scan_website(client=mock_client, website=populated_website)

    mock_handle_traffic.assert_called_once_with(populated_website)
    assert isinstance(report, str)
    assert "Cooldown applied" in report


@pytest.mark.anyio
async def test_scan_website_traffic_error_re_raised_with_params(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that TrafficError is re-raised when delay or concurrent parameters are provided."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanner.get_website_updates",
        side_effect=TrafficError(url=populated_website.url, status_code=429),
    )

    with pytest.raises(TrafficError, match="Scan aborted, try increasing delay or reducing concurrent"):
        await scanner.scan_website(
            client=mock_client,
            website=populated_website,
            delay=1.0,
        )


@pytest.mark.anyio
async def test_scan_website_connection_error_handling(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that WebConnectionError handles unreachable site state and returns a connection error HTML report."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanner.get_website_updates",
        side_effect=WebConnectionError("Connection timed out"),
    )
    mock_handle_conn = mocker.patch.object(WebsiteService, "handle_connection_error", return_value="Site unreachable")

    report = await scanner.scan_website(client=mock_client, website=populated_website)

    mock_handle_conn.assert_called_once_with(populated_website.id)
    assert isinstance(report, str)
    assert "Site unreachable" in report


# ======================================
# scan_all_websites Tests
# ======================================


@pytest.mark.anyio
async def test_scan_all_websites_skips_inactive_cooldown_and_recent_scans(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests that inactive websites, websites on active cooldown, or websites scanned too recently are skipped."""
    inactive_site = populated_website.model_copy(update={"id": 1, "active": False, "url": "https://inactive.com"})
    cooldown_site = populated_website.model_copy(
        update={
            "id": 2,
            "active": True,
            "url": "https://cooldown.com",
            "on_cooldown_until": datetime.now() + timedelta(days=1),
        }
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "id": 3,
            "active": True,
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(),
            "days_between_scans": 7,
        }
    )

    mocker.patch.object(
        WebsiteService,
        "get_all",
        return_value=[inactive_site, cooldown_site, recently_scanned_site],
    )
    mock_scan_website = mocker.patch("app.scanner.scan_website")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    mock_scan_website.assert_not_called()
    mock_send_notification.assert_not_called()
    assert result is None
