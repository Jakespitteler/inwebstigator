from collections.abc import Callable
from email.message import EmailMessage

import httpx2
import pytest
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app import scanner
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
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


def test_send_notification_success(mocker: MockerFixture):
    """Tests that send_notification successfully builds and sends an email, returning None."""
    mock_build_message = mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")

    mock_msg = EmailMessage()
    mock_build_message.return_value = mock_msg

    result = scanner.send_notification("user@example.com", "<p>Scan Report HTML</p>")

    mock_build_message.assert_called_once_with(
        subject="Website Update",
        recipients=["user@example.com"],
        html_body="<p>Scan Report HTML</p>",
    )
    mock_send_email.assert_called_once_with(mock_msg)
    assert result is None


def test_send_notification_failure(mocker: MockerFixture):
    """Tests that send_notification catches exceptions, logs the error, and returns a failure string."""
    mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email")
    mock_send_email.side_effect = Exception("SMTP connection timed out")

    result = scanner.send_notification("user@example.com", "<p>Scan Report HTML</p>")

    mock_send_email.assert_called_once()
    assert result == "Email failed to send."


@pytest.mark.anyio
async def test_scan_website_success(
    session: Session,
    populated_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
):
    """Tests that a successful website scan updates the database and returns the HTML report."""
    async with mock_client_factory(website_handler) as client:
        result = await scanner.scan_website(client=client, session=session, website=populated_website)

    # Verify that an HTML report string is returned
    assert isinstance(result, str)
    assert len(result) > 0

    # Verify that the database record was processed/updated
    db_website = WebsiteService(session).get(populated_website.id)
    assert db_website is not None


@pytest.mark.anyio
async def test_scan_website_traffic_error_triggers_cooldown(
    session: Session,
    test_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
):
    """Tests that hitting a 429 rate limit places the website on a 24-hour cooldown."""
    async with mock_client_factory(rate_limit_handler) as client:
        result = await scanner.scan_website(client=client, session=session, website=test_website)

    assert "website placed on cooldown" in result
    db_website = WebsiteService(session).get(test_website.id)
    assert db_website.on_cooldown_until is not None


@pytest.mark.anyio
@pytest.mark.parametrize("delay, concurrent", [(1.0, None), (None, 5)])
async def test_scan_website_traffic_error_custom_params_aborts(
    session: Session,
    test_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    rate_limit_handler: RequestHandler,
    delay: float | None,
    concurrent: int | None,
):
    """Tests that rate limits with custom parameters abort safely without forcing a cooldown."""
    async with mock_client_factory(rate_limit_handler) as client:
        result = await scanner.scan_website(
            client=client, session=session, website=test_website, delay=delay, concurrent=concurrent
        )

    assert "Scan aborted, try increasing delay" in result
    db_website = WebsiteService(session).get(test_website.id)
    assert db_website.on_cooldown_until is None


@pytest.mark.anyio
async def test_scan_website_connection_error_triggers_cooldown(
    session: Session,
    test_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    connection_error_handler: RequestHandler,
):
    """Tests that a hard network failure places the website on a 2-hour cooldown."""
    async with mock_client_factory(connection_error_handler) as client:
        result = await scanner.scan_website(client=client, session=session, website=test_website)

    assert "website placed on cooldown" in result
    db_website = WebsiteService(session).get(test_website.id)
    assert db_website.on_cooldown_until is not None
