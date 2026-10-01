import uuid
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


def test_send_notification_uses_given_subject(mocker: MockerFixture, test_recipient: RecipientRead):
    """Tests that send_notification passes a custom subject through to the email."""
    mock_build_message = mocker.patch("app.scanner.build_message", return_value=EmailMessage())
    mocker.patch("app.scanner.send_email")
    mocker.patch.object(RecipientService, "get_by_email", return_value=test_recipient)
    mocker.patch.object(RecipientService, "update")

    scanner.send_notification(test_recipient.email, "<p>Body</p>", subject="Website monitoring started")

    mock_build_message.assert_called_once_with(
        subject="Website monitoring started",
        recipients=[test_recipient.email],
        html_body="<p>Body</p>",
    )


# ======================================
# send_monitoring_started_notifications Tests
# ======================================


def test_send_monitoring_started_notifications_emails_each_recipient(
    populated_website: WebsiteRead, test_recipient: RecipientRead, mocker: MockerFixture
):
    """Tests every recipient of the website is sent a confirmation naming the website and its watched pages."""
    other_recipient = test_recipient.model_copy(update={"email": "other@gmail.com", "days_between_health_checks": 14})
    website = populated_website.model_copy(update={"recipients": [test_recipient, other_recipient]})
    mock_send_notification = mocker.patch("app.scanner.send_notification")

    scanner.send_monitoring_started_notifications(website)

    assert [call.args[0] for call in mock_send_notification.call_args_list] == [
        test_recipient.email,
        other_recipient.email,
    ]
    for call in mock_send_notification.call_args_list:
        assert call.kwargs == {"subject": "Website monitoring started"}
        assert website.url in call.args[1]
        assert website.critical_pages[0].url in call.args[1]

    # Each recipient is told their own confirmation interval
    assert "every 7 days" in mock_send_notification.call_args_list[0].args[1]
    assert "every 14 days" in mock_send_notification.call_args_list[1].args[1]


def test_send_monitoring_started_notifications_without_recipients_sends_nothing(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests a website with no recipients (dashboard only) sends no email."""
    website = populated_website.model_copy(update={"recipients": []})
    mock_send_notification = mocker.patch("app.scanner.send_notification")

    scanner.send_monitoring_started_notifications(website)

    mock_send_notification.assert_not_called()


def test_send_monitoring_started_notifications_continues_after_a_failed_send(
    populated_website: WebsiteRead, test_recipient: RecipientRead, mocker: MockerFixture
):
    """Tests a failed send is swallowed, so adding the website still succeeds and other recipients are emailed."""
    other_recipient = test_recipient.model_copy(update={"email": "other@gmail.com"})
    website = populated_website.model_copy(update={"recipients": [test_recipient, other_recipient]})
    mock_send_notification = mocker.patch(
        "app.scanner.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )

    scanner.send_monitoring_started_notifications(website)

    assert mock_send_notification.call_count == 2
    assert mock_send_notification.call_args.args[0] == other_recipient.email


def test_monitoring_started_html_escapes_urls_and_describes_schedule(populated_website: WebsiteRead):
    """Tests the confirmation body escapes URLs and states the scan and confirmation intervals."""
    website = populated_website.model_copy(
        update={"url": "https://example.com/?a=1&b=<script>", "days_between_scans": 1, "critical_pages": []}
    )

    body = scanner._monitoring_started_html(website, days_between_health_checks=7)  # pyright: ignore[reportPrivateUsage]

    assert "https://example.com/?a=1&amp;b=&lt;script&gt;" in body
    assert "<script>" not in body
    assert "checked every day" in body
    assert "every 7 days" in body
    assert "Pages being watched" not in body


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
async def test_adding_a_website_saves_a_baseline_and_only_later_changes_are_reported(
    session: Session, mocker: MockerFixture, api_client: TestClient
):
    """Tests the first scan of a new website records no changes, and the next scan reports only what changed."""
    main_url = "https://example.com"
    html = "<html><body><h2>Fees</h2><p>The fee is $100.</p></body></html>"

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    def mock_client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.frontend.api.routers.AsyncClient", side_effect=mock_client)
    crawl = mocker.patch("app.backend.engine.crawl_site", return_value={main_url, f"{main_url}/about"})

    response = api_client.post("/scanner/initial_scan", json={"url": main_url})
    assert response.status_code == 200, response.text

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    updates_panel = dashboard.select_one("#updates-panel").get_text()
    assert "No changes were detected" in updates_panel
    assert "The fee is $100." not in updates_panel

    # Next scan: the page text changes and a new internal page appears
    html = html.replace("$100", "$120")
    crawl.return_value = {main_url, f"{main_url}/about", f"{main_url}/new-page"}
    website = WebsiteService(session).get_by_url(main_url)
    async with mock_client() as client:
        report = await scanner.scan_website(client, website)

    assert report is not None
    assert "$120" in report
    assert f"{main_url}/new-page" in report
    assert f"{main_url}/about" not in report


@pytest.mark.anyio
async def test_scan_report_only_includes_changes_found_by_that_scan(session: Session, mocker: MockerFixture):
    """Tests a report lists only pages that changed in that scan, not old changes from earlier scans."""
    main_url = "https://example.com"
    pages = {
        main_url: "<html><body><p>Home page.</p></body></html>",
        f"{main_url}/fees": "<html><body><p>The fee is $100.</p></body></html>",
        f"{main_url}/dates": "<html><body><p>Applications close in May.</p></body></html>",
    }

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=pages[str(request.url).rstrip("/")])

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.engine.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=main_url, critical_pages=[f"{main_url}/fees", f"{main_url}/dates"]))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scanner.scan_website(client, website) is None  # baseline

        pages[f"{main_url}/fees"] = pages[f"{main_url}/fees"].replace("$100", "$120")
        first_report = await scanner.scan_website(client, service.get(website.id))
        assert first_report is not None and "$120" in first_report

        pages[f"{main_url}/dates"] = pages[f"{main_url}/dates"].replace("May", "June")
        second_report = await scanner.scan_website(client, service.get(website.id))

    assert second_report is not None
    assert "June" in second_report
    assert "$120" not in second_report
    assert f"{main_url}/fees" not in second_report


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


def _due_website(website: WebsiteRead, url: str, recipient_email: str) -> WebsiteRead:
    """Returns a copy of the website that is due a scan, with a single recipient."""
    recipient = website.recipients[0].model_copy(update={"email": recipient_email})
    return website.model_copy(
        update={
            "id": uuid.uuid4(),
            "url": url,
            "active": True,
            "on_cooldown_until": None,
            "last_scan_at": None,
            "recipients": [recipient],
        }
    )


@pytest.mark.anyio
async def test_scan_all_websites_continues_after_a_website_fails(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests one website's unexpected failure does not stop the others being scanned and reported."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    broken = _due_website(populated_website, "https://broken.com", "broken@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, broken, last])
    mock_scan_website = mocker.patch(
        "app.scanner.scan_website",
        side_effect=[
            "<li>first report</li>",
            httpx2.HTTPStatusError("404", request=None, response=None),
            "<li>last report</li>",
        ],  # type: ignore
    )
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    assert mock_scan_website.call_count == 3
    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["first@gmail.com", "last@gmail.com"]
    assert result == "<li>first report</li><li>last report</li>"

    # The broken website's scan time is still recorded, so it is retried at its normal interval
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [first.id, broken.id, last.id]


@pytest.mark.anyio
async def test_scan_all_websites_continues_after_a_failed_send(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests one recipient's failed email does not stop the remaining recipients being emailed."""
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    second = _due_website(populated_website, "https://second.com", "second@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[first, second])
    mocker.patch("app.scanner.scan_website", side_effect=["<li>first report</li>", "<li>second report</li>"])
    mock_send_notification = mocker.patch(
        "app.scanner.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )
    mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["first@gmail.com", "second@gmail.com"]
    assert result == "<li>first report</li><li>second report</li>"
