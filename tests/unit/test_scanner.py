from collections.abc import Callable
from datetime import datetime
from email.message import EmailMessage

import httpx2
import pytest
from pytest_mock import MockerFixture

from app import scanner
from app.core.errors import TrafficError, WebConnectionError
from app.db.core import db_context
from app.db.services.user_service import UserService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
from app.models.user_models import UserRead
from app.models.website_models import WebsiteRead
from tests.conftest import RequestHandler

# ======================================
# Setup Fixtures
# ======================================


@pytest.fixture
def populated_website(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,
) -> WebsiteRead:
    """Provides a WebsiteRead model fully populated with its internal relationships."""
    return test_website.model_copy(
        update={
            "internal_links": [test_internal_link],
            "critical_pages": [test_critical_page],
        }
    )


# ======================================
# send_notification Tests
# ======================================


def test_send_notification_success(mocker: MockerFixture, test_user: UserRead):
    """Tests that send_notification builds and sends an email, updating user.last_email_at."""
    mock_build_message = mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")
    mock_user_service_update = mocker.patch.object(UserService, "update")

    mock_msg = EmailMessage()
    mock_build_message.return_value = mock_msg

    result = scanner.send_notification(test_user, "<p>Scan Report HTML</p>")

    mock_build_message.assert_called_once_with(
        subject="Website Update",
        recipients=[test_user.email],
        html_body="<p>Scan Report HTML</p>",
    )
    mock_send_email.assert_called_once_with(mock_msg)
    mock_user_service_update.assert_called_once()
    assert result is None


def test_send_notification_failure(mocker: MockerFixture, test_user: UserRead):
    """Tests that send_notification propagates exceptions if email delivery fails, preventing metadata updates."""
    mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")
    mock_send_email.side_effect = Exception("SMTP connection timed out")
    mock_user_service_update = mocker.patch.object(UserService, "update")

    with pytest.raises(Exception, match="SMTP connection timed out"):
        scanner.send_notification(test_user, "<p>Scan Report HTML</p>")

    mock_send_email.assert_called_once()
    mock_user_service_update.assert_not_called()


# ======================================
# scan_website Tests
# ======================================


@pytest.mark.anyio
async def test_scan_website_success(
    populated_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests that a successful website scan updates the database record and returns the HTML report."""
    async with mock_client_factory(website_handler) as client:
        report: str | None = await scanner.scan_website(client=client, website=populated_website)

    assert isinstance(report, str)
    assert len(report) > 0

    with db_context() as session:
        db_website = WebsiteService(session).get(populated_website.id)
    assert db_website is not None


@pytest.mark.anyio
async def test_scan_website_traffic_error_handling(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
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
async def test_scan_website_traffic_error_re_raised_with_params(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
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
async def test_scan_website_connection_error_handling(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
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
# scan_user_websites Tests
# ======================================


@pytest.mark.anyio
async def test_scan_user_websites_success(
    test_user: UserRead,
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests scanning active user websites, updating last_scan_at, and dispatching notification email."""
    user_with_websites = test_user.model_copy(update={"websites": [populated_website]})
    mocker.patch("app.scanner.scan_website", return_value="<li>Website Updated</li>")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mock_user_service_update = mocker.patch.object(UserService, "update")

    result = await scanner.scan_user_websites(user_with_websites)

    assert result == "<ul><li>Website Updated</li></ul>"
    mock_send_notification.assert_called_once_with(user_with_websites, "<ul><li>Website Updated</li></ul>")
    mock_user_service_update.assert_called_once()


@pytest.mark.anyio
async def test_scan_user_websites_skips_inactive_and_cooldown(
    test_user: UserRead,
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests that inactive websites or websites on active cooldown are skipped during scanning."""
    inactive_site = populated_website.model_copy(update={"id": 1, "active": False})
    cooldown_site = populated_website.model_copy(
        update={"id": 2, "active": True, "on_cooldown_until": datetime(2099, 1, 1)}
    )
    user_with_sites = test_user.model_copy(update={"websites": [inactive_site, cooldown_site]})

    mock_scan_website = mocker.patch("app.scanner.scan_website")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(UserService, "update")

    result = await scanner.scan_user_websites(user_with_sites)

    mock_scan_website.assert_not_called()
    mock_send_notification.assert_not_called()
    assert result is None
