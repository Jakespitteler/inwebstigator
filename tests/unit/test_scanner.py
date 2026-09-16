from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx2
import pytest
from sqlalchemy.orm import Session

from app import scanner
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
# Tests: Happy Path & Email Routing
# ======================================


@pytest.mark.anyio
async def test_scan_website_success_explicit_recipient(
    session: Session,
    populated_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
    monkeypatch: pytest.MonkeyPatch,
):
    """Tests successful scan, verifying database updates and email generation to an explicit address."""
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(scanner, "send_email", sent_messages.append)

    async with mock_client_factory(website_handler) as client:
        result = await scanner.scan_website(
            client=client,
            session=session,
            website=populated_website,
            recipient_email="override@example.com",
        )

    # Verify Report Output
    assert "<div" in result
    assert "Website monitoring report" in result
    assert "test_critical_page" in result
    assert "Updated Critical Page Content" in result

    # Verify Database Updates (check the actual relation, not ephemeral diff lists)
    db_website = WebsiteService(session).get(populated_website.id)
    assert db_website.internal_links is not None
    assert len(db_website.internal_links) > 1
    assert any("page1.html" in link.url for link in db_website.internal_links)

    # Verify Email Routing
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "override@example.com"


@pytest.mark.anyio
async def test_scan_website_success_fallback_email(
    session: Session,
    populated_website: WebsiteRead,
    test_user: UserRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
    monkeypatch: pytest.MonkeyPatch,
):
    """Tests that a scan defaults to the website owner's database email when not provided."""
    sent_messages: list[EmailMessage] = []
    monkeypatch.setattr(scanner, "send_email", sent_messages.append)

    async with mock_client_factory(website_handler) as client:
        await scanner.scan_website(client=client, session=session, website=populated_website)

    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == test_user.email


# ======================================
# Tests: Traffic & Connection Errors
# ======================================


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


# ======================================
# Tests: Email Exceptions
# ======================================


@pytest.mark.anyio
async def test_scan_website_email_delivery_failure(
    session: Session,
    populated_website: WebsiteRead,
    mock_client_factory: Callable[[RequestHandler], httpx2.AsyncClient],
    website_handler: RequestHandler,
    monkeypatch: pytest.MonkeyPatch,
):
    """Tests that a raised exception during SMTP email sending is caught and returned."""

    def raise_smtp_error(*_: Any, **__: Any) -> None:
        raise RuntimeError("SMTP Connection Refused")

    monkeypatch.setattr(scanner, "send_email", raise_smtp_error)

    async with mock_client_factory(website_handler) as client:
        result = await scanner.scan_website(client=client, session=session, website=populated_website)

    assert result == "Email failed to send."

    # Verify DB still updated successfully despite email failure
    db_website = WebsiteService(session).get(populated_website.id)
    assert db_website.internal_links is not None
    assert len(db_website.internal_links) > 1
    assert any("page1.html" in link.url for link in db_website.internal_links)
