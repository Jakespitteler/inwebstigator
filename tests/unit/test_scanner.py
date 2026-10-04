import asyncio
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
from app.backend.format_message import monitoring_started_html
from app.core.errors import ScanAlreadyQueuedError, ScanCancelledError, TrafficError, WebConnectionError
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate

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
            "critical_pages": [test_critical_page],
            "recipients": [test_recipient],
        }
    )


# ======================================
# send_notification Tests
# ======================================


def test_send_notification_success(mocker: MockerFixture, test_recipient: RecipientRead):
    """Tests that send_notification builds and sends an email with the given subject, updating last_email_at."""
    mock_msg = EmailMessage()
    mock_build_message = mocker.patch("app.scanner.build_message", return_value=mock_msg)
    mock_send_email = mocker.patch("app.scanner.send_email")
    mocker.patch.object(RecipientService, "get_by_email", return_value=test_recipient)
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    result = scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>", subject="Website Update")

    mock_build_message.assert_called_once_with(
        subject="Website Update",
        recipients=[test_recipient.email],
        html_body="<p>Scan Report HTML</p>",
    )
    mock_send_email.assert_called_once_with(msg=mock_msg)
    mock_recipient_service_update.assert_called_once()
    assert mock_recipient_service_update.call_args.kwargs["id"] == test_recipient.id
    assert result is None


def test_send_notification_failure(mocker: MockerFixture, test_recipient: RecipientRead):
    """Tests that send_notification propagates exceptions if email delivery fails, preventing metadata updates."""
    mocker.patch("app.scanner.build_message")
    mock_send_email = mocker.patch("app.scanner.send_email", side_effect=Exception("SMTP connection timed out"))
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    with pytest.raises(Exception, match="SMTP connection timed out"):
        scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>", subject="Website Update")

    mock_send_email.assert_called_once()
    mock_recipient_service_update.assert_not_called()


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

    calls = mock_send_notification.call_args_list
    assert [call.kwargs["recipient_email"] for call in calls] == [test_recipient.email, other_recipient.email]
    for call in calls:
        assert call.kwargs["subject"] == "Website monitoring started"
        assert website.url in call.kwargs["report"]
        assert website.critical_pages[0].url in call.kwargs["report"]

    # Each recipient is told their own confirmation interval
    assert "every 7 days" in calls[0].kwargs["report"]
    assert "every 14 days" in calls[1].kwargs["report"]


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
    assert mock_send_notification.call_args.kwargs["recipient_email"] == other_recipient.email


def test_monitoring_started_html_escapes_urls_and_describes_schedule(populated_website: WebsiteRead):
    """Tests the confirmation body escapes URLs and states the scan and confirmation intervals."""
    website = populated_website.model_copy(
        update={"url": "https://example.com/?a=1&b=<script>", "days_between_scans": 1, "critical_pages": []}
    )

    body = monitoring_started_html(website, 7)

    assert "https://example.com/?a=1&amp;b=&lt;script&gt;" in body
    assert "<script>" not in body
    assert "checked every day" in body
    assert "every 7 days" in body
    assert "Pages being watched" not in body


# ======================================
# scan_website Tests
# ======================================


@pytest.mark.anyio
async def test_main_url_content_is_scanned_and_shown_in_updates(
    session: Session, mocker: MockerFixture, api_client: TestClient
):
    """Scan the starting page without manually adding it, then detect a real content change."""
    main_url = "https://example.com/au?edition=local"
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=main_url))

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.engine.crawl_site", return_value=set())
    requested_urls: list[str] = []
    html = "<html><body><p>Original main page content.</p></body></html>"

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scanner.scan_website(client, website) is None  # baseline
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
        assert report is not None
        assert "New main-page announcement." in report

        response = api_client.get("/")
        assert response.status_code == 200
        dashboard = BeautifulSoup(response.text, "html.parser")

        updates_panel_tag = dashboard.select_one("#updates-panel")
        assert updates_panel_tag
        assert "New main-page announcement." in updates_panel_tag.get_text()

        website_panel_tag = dashboard.select_one("#websites-panel")
        assert website_panel_tag
        assert "Main website (automatic)" in website_panel_tag.get_text()

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
    updates_panel_tag = dashboard.select_one("#updates-panel")
    assert updates_panel_tag
    updates_panel = updates_panel_tag.get_text()
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


def test_create_monitors_main_url_once(session: Session):
    """Tests the main URL is always a critical page, without duplicates, and is in the returned website."""
    payload = WebsiteCreate(
        url="https://example.com", critical_pages=["https://example.com/fees", "https://example.com"]
    )

    website = WebsiteService(session).create(payload)

    assert sorted(page.url for page in website.critical_pages) == ["https://example.com", "https://example.com/fees"]
    assert payload.critical_pages == ["https://example.com/fees", "https://example.com"]  # caller's model untouched


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
async def test_scan_after_a_failed_first_scan_saves_a_baseline_instead_of_reporting_everything(
    session: Session, mocker: MockerFixture
):
    """Tests a website whose first scan failed is baselined by its next scan, not reported as entirely new."""
    main_url = "https://example.com"
    crawled = {main_url, f"{main_url}/about"}
    html = "<html><body><p>The fee is $100.</p></body></html>"

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.engine.crawl_site", side_effect=WebConnectionError("Connection timed out"))
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=main_url))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=html))
    ) as client:
        assert await scanner.scan_website(client, website) is not None  # the connection error is reported

        crawl.side_effect = None
        crawl.return_value = crawled
        assert await scanner.scan_website(client, service.get(website.id)) is None

    saved = service.get(website.id)
    assert {link.url for link in saved.internal_links} == crawled
    assert saved.critical_pages[0].text_body == html


@pytest.mark.anyio
@pytest.mark.parametrize(
    "website_updates",
    [
        None,
        WebsiteUpdate(initial_internal_links=["https://www.test_website.com/"]),
        WebsiteUpdate(
            critical_page_updates={
                uuid.uuid4(): CriticalPageUpdate(
                    url="https://www.test_website.com/new-page", text_body="<p>New page.</p>", links=[], documents=[]
                )
            }
        ),
    ],
    ids=["nothing-changed", "internal-link-baseline", "new-page-baseline"],
)
async def test_scan_website_without_changes_returns_no_report(
    populated_website: WebsiteRead, mocker: MockerFixture, website_updates: WebsiteUpdate | None
):
    """Tests a scan that finds nothing, or only saves baselines, sends no report but still clears past failures."""
    mocker.patch("app.scanner.get_website_updates", return_value=website_updates)
    mock_update = mocker.patch.object(WebsiteService, "update", return_value=populated_website)
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")
    mock_generate_report = mocker.patch("app.scanner.generate_scan_report_html")

    report = await scanner.scan_website(client=mocker.AsyncMock(spec=httpx2.AsyncClient), website=populated_website)

    assert report is None
    assert mock_update.called is (website_updates is not None)  # baselines are still saved
    mock_reset_failed_attempts.assert_called_once_with(populated_website.id)
    mock_generate_report.assert_not_called()


@pytest.mark.anyio
async def test_scan_website_traffic_error_handling(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that TrafficError triggers cooldown handling and returns a traffic error HTML report."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanner.get_website_updates",
        side_effect=TrafficError(url=populated_website.url, status_code=429),
    )
    mock_handle_traffic = mocker.patch.object(WebsiteService, "handle_traffic_error", return_value="Cooldown applied")
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    report = await scanner.scan_website(client=mock_client, website=populated_website)

    mock_handle_traffic.assert_called_once_with(populated_website)
    mock_reset_failed_attempts.assert_not_called()
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
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    report = await scanner.scan_website(client=mock_client, website=populated_website)

    mock_handle_conn.assert_called_once_with(populated_website.id)
    mock_reset_failed_attempts.assert_not_called()
    assert isinstance(report, str)
    assert "Site unreachable" in report


@pytest.mark.anyio
async def test_scans_run_one_at_a_time_in_the_order_requested(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests a scan requested while another is running waits for it to finish instead of running alongside it."""
    events: list[str] = []

    async def crawl(client: httpx2.AsyncClient, website: WebsiteRead, *args: object) -> None:
        events.append(f"start {website.url}")
        await asyncio.sleep(0.01)
        events.append(f"finish {website.url}")

    mocker.patch("app.scanner.scan_lock", asyncio.Lock())  # A lock for this test's event loop
    mocker.patch("app.scanner.get_website_updates", side_effect=crawl)
    mocker.patch.object(WebsiteService, "reset_failed_attempts")
    second_website = populated_website.model_copy(update={"url": "https://second.com"})
    client = mocker.AsyncMock(spec=httpx2.AsyncClient)

    await asyncio.gather(scanner.scan_website(client, populated_website), scanner.scan_website(client, second_website))

    assert events == [
        f"start {populated_website.url}",
        f"finish {populated_website.url}",
        "start https://second.com",
        "finish https://second.com",
    ]


@pytest.mark.anyio
async def test_a_website_already_queued_or_being_scanned_is_not_queued_again(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests the same website cannot be in the scan queue twice, whether it is being scanned or still waiting."""
    finish_scan = asyncio.Event()

    async def crawl(*args: object) -> None:
        await finish_scan.wait()

    mocker.patch("app.scanner.scan_lock", asyncio.Lock())  # A lock for this test's event loop
    mocker.patch("app.scanner.get_website_updates", side_effect=crawl)
    mocker.patch.object(WebsiteService, "reset_failed_attempts")
    waiting_website = populated_website.model_copy(update={"url": "https://waiting.com"})
    client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    scans = [
        asyncio.create_task(scanner.scan_website(client, website)) for website in (populated_website, waiting_website)
    ]
    await asyncio.sleep(0)

    for website in (populated_website, waiting_website):
        with pytest.raises(ScanAlreadyQueuedError):
            await scanner.scan_website(client, website)

    finish_scan.set()
    await asyncio.gather(*scans)
    assert scanner.queued_crawls == {}  # Both can be queued again now they have finished


@pytest.mark.anyio
async def test_cancelling_a_scan_stops_it_and_saves_nothing(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests a scan can be cancelled whether it is running or still queued, and nothing from it is saved."""
    crawls_started: list[str] = []
    first_crawl_started = asyncio.Event()

    async def crawl(client: httpx2.AsyncClient, website: WebsiteRead, *args: object) -> None:
        crawls_started.append(website.url)
        first_crawl_started.set()
        await asyncio.Event().wait()  # Runs until cancelled

    mocker.patch("app.scanner.scan_lock", asyncio.Lock())  # A lock for this test's event loop
    mocker.patch("app.scanner.get_website_updates", side_effect=crawl)
    mock_update = mocker.patch.object(WebsiteService, "update")
    queued_website = populated_website.model_copy(update={"url": "https://queued.com"})
    client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    scans = [
        asyncio.create_task(scanner.scan_website(client, website)) for website in (populated_website, queued_website)
    ]
    await first_crawl_started.wait()

    assert scanner.cancel_scan(queued_website.url)
    assert scanner.cancel_scan(populated_website.url)

    for scan in scans:
        with pytest.raises(ScanCancelledError):
            await scan
    assert crawls_started == [populated_website.url]  # The queued scan never started
    mock_update.assert_not_called()
    assert scanner.queued_crawls == {}
    assert not scanner.cancel_scan(populated_website.url)  # Nothing left to cancel


@pytest.mark.anyio
async def test_stopping_the_app_mid_scan_is_not_mistaken_for_cancelling_the_scan(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests a scan stopped because the app is shutting down is cancelled as normal, rather than being
    reported as a scan the user cancelled."""
    crawl_started = asyncio.Event()

    async def crawl(*args: object) -> None:
        crawl_started.set()
        await asyncio.Event().wait()  # Runs until cancelled

    mocker.patch("app.scanner.scan_lock", asyncio.Lock())  # A lock for this test's event loop
    mocker.patch("app.scanner.get_website_updates", side_effect=crawl)
    scan = asyncio.create_task(scanner.scan_website(mocker.AsyncMock(spec=httpx2.AsyncClient), populated_website))
    await crawl_started.wait()

    scan.cancel()

    with pytest.raises(asyncio.CancelledError):
        await scan
    assert scanner.queued_crawls == {}


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
        side_effect=["<li>first report</li>", RuntimeError("Unexpected scan failure"), "<li>last report</li>"],
    )
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    assert mock_scan_website.call_count == 3
    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["first@gmail.com", "last@gmail.com"]
    assert all(call.kwargs["subject"] == "Website Update" for call in mock_send_notification.call_args_list)
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


@pytest.mark.anyio
async def test_scan_all_websites_returns_reports_for_websites_without_recipients(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a dashboard-only website (no recipients) still has its report returned, without sending email."""
    website = _due_website(populated_website, "https://dashboard-only.com", "unused@gmail.com").model_copy(
        update={"recipients": []}
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[website])
    mocker.patch("app.scanner.scan_website", return_value="<li>dashboard report</li>")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    mock_send_notification.assert_not_called()
    assert result == "<li>dashboard report</li>"
