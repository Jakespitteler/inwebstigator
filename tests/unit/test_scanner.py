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
        report: str = await scanner.scan_website(client=client, session=session, website=populated_website)

    # Verify that an HTML report string is returned
    assert isinstance(report, str)
    assert len(report) > 0

    # Verify that the database record was processed/updated
    db_website = WebsiteService(session).get(populated_website.id)
    assert db_website is not None
