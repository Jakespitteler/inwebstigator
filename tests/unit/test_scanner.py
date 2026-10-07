import asyncio
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta

import httpx2
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app import scanner
from app.backend.email_service.html_bodies import monitoring_started_html
from app.backend.email_service.message_builder import OutgoingEmail
from app.core.errors import (
    NotFoundError,
    ScanAlreadyQueuedError,
    ScanCancelledError,
    TrafficError,
    WebConnectionError,
    WebsiteTooLargeError,
)
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead, WebsiteUpdate
from tests.fakes import FakeEmailSender

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
            "internal_link_count": 1,  # test_internal_link
            "critical_pages": [test_critical_page],
            "recipients": [test_recipient],
        }
    )


@pytest.fixture
def websites_unchanged_during_run(mocker: MockerFixture) -> None:
    """Makes a scan of all websites use each website as it was listed, as if none changed during the run."""

    def as_listed(website: WebsiteRead) -> WebsiteRead:
        return website

    mocker.patch("app.scanner._get_latest_state", side_effect=as_listed)


# ======================================
# send_notification Tests
# ======================================


def test_send_notification_success(
    mocker: MockerFixture, test_recipient: RecipientRead, email_sender: FakeEmailSender
) -> None:
    """Tests that send_notification sends an email with the given subject, then updates last_email_at."""
    mocker.patch.object(RecipientService, "get_by_email", return_value=test_recipient)
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>", "Website update", email_sender)

    assert email_sender.sent == [
        OutgoingEmail(to=test_recipient.email, subject="Website update", html_body="<p>Scan Report HTML</p>")
    ]
    mock_recipient_service_update.assert_called_once()
    assert mock_recipient_service_update.call_args.kwargs["id"] == test_recipient.id


def test_send_notification_failure(mocker: MockerFixture, test_recipient: RecipientRead) -> None:
    """Tests that send_notification propagates exceptions if email delivery fails, preventing metadata updates."""

    class FailingSender(FakeEmailSender):
        def send(self, email: OutgoingEmail) -> None:
            raise ConnectionError("SMTP connection timed out")

    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    with pytest.raises(ConnectionError, match="SMTP connection timed out"):
        scanner.send_notification(test_recipient.email, "<p>Scan Report HTML</p>", "Website update", FailingSender())

    mock_recipient_service_update.assert_not_called()


def test_send_notification_does_not_report_a_sent_email_as_failed(
    mocker: MockerFixture, test_recipient: RecipientRead, email_sender: FakeEmailSender
) -> None:
    """Tests that an email that went out is not reported as failed when recording it fails afterwards."""
    mocker.patch.object(RecipientService, "get_by_email", side_effect=RuntimeError("Database is locked"))

    scanner.send_notification(test_recipient.email, "<p>Report</p>", "Website update", email_sender)

    assert len(email_sender.sent) == 1


def test_send_report_to_recipients_uses_one_connection(email_sender: FakeEmailSender, mocker: MockerFixture) -> None:
    """Tests a report emailed to several recipients is sent over one connection to the mail server."""
    mocker.patch("app.scanner._record_email_sent")

    scanner.send_report_to_recipients(["a@gmail.com", "b@gmail.com"], "<p>Report</p>", "Manual scan", email_sender)

    assert [email.to for email in email_sender.sent] == ["a@gmail.com", "b@gmail.com"]
    assert email_sender.connections_opened == 1


# ======================================
# monitoring_started_html Tests
# ======================================


def test_monitoring_started_html_lists_the_main_page_and_critical_pages():
    """Tests the email lists the website's main page, which is always watched, then its other critical pages,
    each only once (here "/" is the main page again)."""
    body = monitoring_started_html(WebsiteCreate(url=HttpUrl("https://example.com"), critical_pages=["/news", "/"]), 7)

    assert body.count("<li") == 2
    assert body.index("https://example.com/<") < body.index("https://example.com/news<")


def test_monitoring_started_html_escapes_urls_and_describes_schedule():
    """Tests the email body escapes URLs and states the scan and health check intervals."""
    website = WebsiteCreate(url=HttpUrl("https://example.com/?a=1&b=<script>"), days_between_scans=1)

    body = monitoring_started_html(website, 7)

    assert "https://example.com/?a=1&amp;b=%3Cscript%3E" in body  # pydantic percent-encodes the "<" and ">"
    assert "<script>" not in body
    assert "checked every day" in body
    assert "every 7 days" in body


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
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.change_detection.crawl_site", return_value=set())
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
        assert main_page.url == HttpUrl(main_url)
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
    crawl = mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url, f"{main_url}/about"})

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
    website = WebsiteService(session).get_by_url(HttpUrl(main_url))
    async with mock_client() as client:
        report = await scanner.scan_website(client, website)

    assert report is not None
    assert "$120" in report
    assert f"{main_url}/new-page" in report
    assert f"{main_url}/about" not in report


def test_create_monitors_main_url_once(session: Session):
    """Tests the main URL is always a critical page, without duplicates, and is in the returned website."""
    payload = WebsiteCreate(
        url=HttpUrl("https://example.com"), critical_pages=["https://example.com/fees", "https://example.com"]
    )

    website = WebsiteService(session).create(payload)

    assert sorted(str(page.url) for page in website.critical_pages) == [
        "https://example.com/",
        "https://example.com/fees",
    ]
    assert payload.critical_pages == ["https://example.com/fees", "https://example.com/"]  # caller's model untouched


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
    mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(
        WebsiteCreate(url=HttpUrl(main_url), critical_pages=[f"{main_url}/fees", f"{main_url}/dates"])
    )

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
async def test_unreachable_critical_page_is_reported_once_and_reset_when_back(session: Session, mocker: MockerFixture):
    """Tests a failing critical page is only reported once it has failed scans in a row, is not reported
    again while it stays down, and has its failure count reset (keeping its last good copy) once it is back."""
    main_url = "https://example.com"
    fees_url = f"{main_url}/fees"
    fees_html = "<html><body><p>The fee is $100.</p></body></html>"
    fees_status_code = 200

    def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url).rstrip("/") == fees_url:
            return httpx2.Response(fees_status_code, text=fees_html)
        return httpx2.Response(200, text="<html><body><p>Home page.</p></body></html>")

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url), critical_pages=[fees_url]))

    def fees_page() -> CriticalPageRead:
        return next(page for page in service.get(website.id).critical_pages if page.url == HttpUrl(fees_url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scanner.scan_website(client, website) is None  # baseline

        fees_status_code = 500
        assert await scanner.scan_website(client, service.get(website.id)) is None  # may be a blip
        report = await scanner.scan_website(client, service.get(website.id))
        assert report is not None
        assert "Watched Pages Unreachable (1)" in report
        assert "HTTP 500" in report
        assert await scanner.scan_website(client, service.get(website.id)) is None  # not reported again
        assert fees_page().consecutive_failures == 3

        fees_status_code = 200
        assert await scanner.scan_website(client, service.get(website.id)) is None

    assert fees_page().consecutive_failures == 0
    assert fees_page().last_failure_reason is None
    assert fees_page().text_body == fees_html


@pytest.mark.anyio
async def test_scan_after_a_failed_first_scan_saves_a_baseline_instead_of_reporting_everything(
    session: Session, mocker: MockerFixture
):
    """Tests a website whose first scan failed is baselined by its next scan, not reported as entirely new."""
    main_url = "https://example.com"
    crawled = {f"{main_url}/", f"{main_url}/about"}
    html = "<html><body><p>The fee is $100.</p></body></html>"

    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch(
        "app.backend.change_detection.crawl_site", side_effect=WebConnectionError("Connection timed out")
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=html))
    ) as client:
        assert await scanner.scan_website(client, website) is not None  # the connection error is reported

        crawl.side_effect = None
        crawl.return_value = crawled
        assert await scanner.scan_website(client, service.get(website.id)) is None

    saved = service.get(website.id)
    assert set(InternalLinkService(session).get_urls_for_website(website.id)) == crawled
    assert saved.critical_pages[0].text_body == html


@pytest.mark.anyio
@pytest.mark.parametrize(
    "website_updates",
    [
        None,
        WebsiteUpdate(initial_internal_links=[HttpUrl("https://www.test_website.com/")]),
        WebsiteUpdate(
            critical_page_updates={
                uuid.uuid4(): CriticalPageUpdate(
                    url=HttpUrl("https://www.test_website.com/new-page"),
                    text_body="<p>New page.</p>",
                    links=[],
                    documents=[],
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
        side_effect=TrafficError(url=str(populated_website.url), status_code=429),
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
        side_effect=TrafficError(url=str(populated_website.url), status_code=429),
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
async def test_scan_website_deactivates_a_website_too_large_to_scan(session: Session, mocker: MockerFixture):
    """Tests a website with more pages than the crawler will scan is deactivated, saying why, and its critical
    pages are still checked straight away, without saving anything from the refused crawl."""
    main_url = "https://example.com"
    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch(
        "app.backend.change_detection.crawl_site", side_effect=WebsiteTooLargeError(main_url, max_pages=50_000)
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text="<p>Home page.</p>"))
    ) as client:
        report = await scanner.scan_website(client, website)

    assert report is not None
    assert "Website Too Large" in report
    assert "more than 50,000 pages" in report
    assert "critical pages are still checked" in report

    saved = service.get(website.id)
    assert saved.active is False
    assert saved.deactivated_reason == DeactivationReason.TOO_LARGE
    assert saved.internal_link_count == 0
    assert saved.critical_pages[0].text_body == "<p>Home page.</p>"  # Its baseline is saved for the next scan


@pytest.mark.anyio
async def test_changes_found_when_a_website_becomes_too_large_are_still_reported(
    session: Session, mocker: MockerFixture
):
    """Tests the scan that finds a website has grown too large still reports changes on its critical pages."""
    main_url = "https://example.com"
    html = "<html><body><p>The fee is $100.</p></body></html>"
    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scanner.scan_website(client, website) is None  # baseline

        html = html.replace("$100", "$120")
        crawl.side_effect = WebsiteTooLargeError(main_url, max_pages=50_000)
        report = await scanner.scan_website(client, service.get(website.id))

    assert report is not None
    assert "Website Too Large" in report
    assert "$120" in report
    assert crawl.call_count == 2  # The critical pages were checked again without crawling the website


@pytest.mark.anyio
async def test_inactive_website_only_has_its_critical_pages_scanned(session: Session, mocker: MockerFixture):
    """Tests an inactive website is not crawled, but changes on its critical pages are still found and reported."""
    main_url = "https://example.com"
    html = "<html><body><p>Applications close in May.</p></body></html>"
    mocker.patch("app.scanner.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.change_detection.crawl_site")
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))
    service.update(id=website.id, model_update=WebsiteUpdate(active=False))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scanner.scan_website(client, service.get(website.id)) is None  # baseline

        html = html.replace("May", "June")
        report = await scanner.scan_website(client, service.get(website.id))

    assert report is not None
    assert "June" in report
    crawl.assert_not_called()
    saved = service.get(website.id)
    assert saved.active is False
    assert saved.internal_link_count == 0


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
        crawls_started.append(str(website.url))
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

    assert scanner.cancel_scan(str(queued_website.url))
    assert scanner.cancel_scan(str(populated_website.url))

    for scan in scans:
        with pytest.raises(ScanCancelledError):
            await scan
    assert crawls_started == [str(populated_website.url)]  # The queued scan never started
    mock_update.assert_not_called()
    assert scanner.queued_crawls == {}
    assert not scanner.cancel_scan(str(populated_website.url))  # Nothing left to cancel


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
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_skips_cooldown_and_recent_scans(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests that websites on active cooldown, or websites scanned too recently are skipped."""
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
        return_value=[cooldown_site, recently_scanned_site],
    )
    mock_scan_website = mocker.patch("app.scanner.scan_website")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    mock_scan_website.assert_not_called()
    mock_send_notification.assert_not_called()
    assert result is None


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_ignoring_the_schedule_scans_websites_not_due_but_skips_cooldown(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests "Run All Scans" also scans websites scanned too recently, while websites on cooldown are still skipped."""
    cooldown_site = populated_website.model_copy(
        update={"url": "https://cooldown.com", "on_cooldown_until": datetime.now() + timedelta(days=1)}
    )
    recently_scanned_site = populated_website.model_copy(
        update={
            "url": "https://scanned.com",
            "on_cooldown_until": None,
            "last_scan_at": datetime.now(),
            "days_between_scans": 7,
        }
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[cooldown_site, recently_scanned_site])
    mock_scan_website = mocker.patch("app.scanner.scan_website", return_value=None)
    mocker.patch.object(WebsiteService, "update")

    await scanner.scan_all_websites(ignore_schedule=True)

    mock_scan_website.assert_awaited_once()
    assert mock_scan_website.call_args.args[1].url == recently_scanned_site.url


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
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_continues_after_a_website_fails(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests one website's unexpected failure does not stop the others being scanned and reported, and its
    recipients are told the scan failed rather than being left to think nothing changed."""
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
    sent_to = [call.args[0] for call in mock_send_notification.call_args_list]
    assert sent_to == ["first@gmail.com", "broken@gmail.com", "last@gmail.com"]
    assert [call.kwargs["subject"] for call in mock_send_notification.call_args_list] == [
        "Website update: first.com",
        "Website update: broken.com",
        "Website update: last.com",
    ]
    failure_report: str = mock_send_notification.call_args_list[1].kwargs["html_body"]
    assert "Scan Failure Report" in failure_report
    assert "https://broken.com" in failure_report
    assert result is not None
    assert result.startswith("<li>first report</li>")
    assert result.endswith("<li>last report</li>")

    # The broken website's scan time is still recorded, so it is retried at its normal interval
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [first.id, broken.id, last.id]


@pytest.mark.anyio
async def test_scan_all_websites_continues_after_a_database_error(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website that cannot be read, or whose scan time cannot be saved, does not end the run, so the
    other websites are still scanned and the reports already found are still emailed."""
    unreadable = _due_website(populated_website, "https://unreadable.com", "unreadable@gmail.com")
    first = _due_website(populated_website, "https://first.com", "first@gmail.com")
    last = _due_website(populated_website, "https://last.com", "last@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[unreadable, first, last])

    def get_latest(website: WebsiteRead) -> WebsiteRead:
        if website.id == unreadable.id:
            raise RuntimeError("database is locked")
        return website

    mocker.patch("app.scanner._get_latest_state", side_effect=get_latest)
    mock_scan_website = mocker.patch(
        "app.scanner.scan_website", side_effect=["<li>first report</li>", "<li>last report</li>"]
    )
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(WebsiteService, "update", side_effect=[RuntimeError("database is locked"), None])

    result = await scanner.scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [first, last]
    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["first@gmail.com", "last@gmail.com"]
    assert result == "<li>first report</li><li>last report</li>"


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_reads_every_website(mocker: MockerFixture):
    """Tests every website is read for the run, not just the first page of 100."""
    mock_get_all = mocker.patch.object(WebsiteService, "get_all", return_value=[])

    await scanner.scan_all_websites()

    mock_get_all.assert_called_once_with(limit=None)


@pytest.mark.parametrize(
    ("days_between_scans", "last_scan_at", "run_started_at", "expected_due"),
    [
        (1, None, datetime(2026, 1, 1, 8, 0), True),
        (1, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 8, 0), False),
        (1, datetime(2026, 1, 1, 8, 0, 5), datetime(2026, 1, 2, 8, 0, 2), True),
        (1.5, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 20, 0), False),
        (1.5, datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 3, 8, 0), True),
        (0.5, datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 20, 0), True),
        (0.5, datetime(2026, 1, 1, 15, 0), datetime(2026, 1, 1, 20, 0), False),
    ],
    ids=[
        "never-scanned",
        "a-day-not-yet-passed",
        "run-started-a-few-seconds-earlier-than-last-time",
        "a-day-and-a-half-not-yet-passed",
        "a-day-and-a-half-passed",
        "half-a-day-passed",
        "half-a-day-not-yet-passed",
    ],
)
def test_is_due_a_scan_counts_from_the_exact_time_of_the_last_scan(
    populated_website: WebsiteRead,
    days_between_scans: float,
    last_scan_at: datetime | None,
    run_started_at: datetime,
    expected_due: bool,
):
    """Tests a website is due a scan once its interval has passed since its last scan, so intervals such as 1.5
    days are not rounded down to whole days, while a run starting slightly early still counts."""
    website = populated_website.model_copy(
        update={"days_between_scans": days_between_scans, "last_scan_at": last_scan_at}
    )

    assert scanner._is_due_a_scan(website, run_started_at) is expected_due  # pyright: ignore[reportPrivateUsage]


def test_is_due_a_scan_tolerance_is_at_most_half_the_time_between_runs(
    populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests that when runs are minutes apart, a website set to scan every half hour is scanned at the run nearest
    to when it is due, rather than at every run because of the usual hour's tolerance."""
    mocker.patch.object(scanner.config, "scheduler_minimum_days_between_scans", 0.0034)  # Runs every ~4.9 minutes
    last_scan_at = datetime(2026, 1, 1, 8, 0)
    website = populated_website.model_copy(update={"days_between_scans": 0.02, "last_scan_at": last_scan_at})
    runs = [last_scan_at + timedelta(days=0.0034) * run for run in range(1, 8)]

    due_runs = [run for run in runs if scanner._is_due_a_scan(website, run)]  # pyright: ignore[reportPrivateUsage]

    assert due_runs[0] == runs[5]  # The 6th run, 29.4 minutes after the last scan, is the nearest to 28.8 minutes


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
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
@pytest.mark.usefixtures("websites_unchanged_during_run")
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


@pytest.mark.anyio
async def test_scan_all_websites_skips_websites_deleted_during_the_run(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website deleted after the run started is not scanned, as it is checked again just before its turn."""
    deleted = _due_website(populated_website, "https://deleted.com", "deleted@gmail.com")
    kept = _due_website(populated_website, "https://kept.com", "kept@gmail.com")
    mocker.patch.object(WebsiteService, "get_all", return_value=[deleted, kept])

    def get_latest(id: uuid.UUID) -> WebsiteRead:
        if id == deleted.id:
            raise NotFoundError(id=id)
        return kept

    mocker.patch.object(WebsiteService, "get", side_effect=get_latest)
    mock_scan_website = mocker.patch("app.scanner.scan_website", return_value="<li>kept report</li>")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mock_update = mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [kept]
    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["kept@gmail.com"]
    assert [call.kwargs["id"] for call in mock_update.call_args_list] == [kept.id]
    assert result == "<li>kept report</li>"


@pytest.mark.anyio
async def test_scan_all_websites_uses_settings_changed_during_the_run(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests a website put on cooldown after the run started is skipped, as it is checked again just before its
    turn."""
    website = _due_website(populated_website, "https://cooldown.com", "cooldown@gmail.com")
    cooled_down = website.model_copy(update={"on_cooldown_until": datetime.now() + timedelta(hours=2)})
    mocker.patch.object(WebsiteService, "get_all", return_value=[website])
    mocker.patch.object(WebsiteService, "get", return_value=cooled_down)
    mock_scan_website = mocker.patch("app.scanner.scan_website")

    assert await scanner.scan_all_websites() is None

    mock_scan_website.assert_not_called()


@pytest.mark.anyio
@pytest.mark.usefixtures("websites_unchanged_during_run")
async def test_scan_all_websites_still_scans_inactive_websites(
    populated_website: WebsiteRead,
    mocker: MockerFixture,
):
    """Tests an inactive website is still scanned (for its critical pages) and its report emailed."""
    inactive = _due_website(populated_website, "https://inactive.com", "inactive@gmail.com").model_copy(
        update={"active": False}
    )
    mocker.patch.object(WebsiteService, "get_all", return_value=[inactive])
    mock_scan_website = mocker.patch("app.scanner.scan_website", return_value="<li>critical page report</li>")
    mock_send_notification = mocker.patch("app.scanner.send_notification")
    mocker.patch.object(WebsiteService, "update")

    result = await scanner.scan_all_websites()

    assert [call.args[1] for call in mock_scan_website.call_args_list] == [inactive]
    assert [call.args[0] for call in mock_send_notification.call_args_list] == ["inactive@gmail.com"]
    assert result == "<li>critical page report</li>"
