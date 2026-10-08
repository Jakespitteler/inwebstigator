import asyncio
import uuid
from contextlib import nullcontext

import httpx2
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.core.errors import ScanCancelledError, TrafficError, WebConnectionError, WebsiteTooLargeError
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.scanning.scan_queue import ScanQueue
from app.scanning.website_scan import scan_website


@pytest.mark.anyio
async def test_main_url_content_is_scanned_and_shown_in_updates(
    session: Session, mocker: MockerFixture, api_client: TestClient
):
    """Scan the starting page without manually adding it, then detect a real content change."""
    main_url = "https://example.com/au?edition=local"
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.change_detection.crawl_site", return_value=set())
    requested_urls: list[str] = []
    html = "<html><body><p>Original main page content.</p></body></html>"

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scan_website(client, website) is None  # baseline
        baseline = service.get(website.id)
        assert len(baseline.critical_pages) == 1
        main_page = baseline.critical_pages[0]
        assert main_page.url == HttpUrl(main_url)
        assert main_page.text_body == html

        html = html.replace("</body>", "<p>New main-page announcement.</p></body>")
        report = await scan_website(client, baseline)
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

        assert await scan_website(client, updated) is None

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

    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
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
        report = await scan_website(client, website)

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

    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(
        WebsiteCreate(url=HttpUrl(main_url), critical_pages=[f"{main_url}/fees", f"{main_url}/dates"])
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scan_website(client, website) is None  # baseline

        pages[f"{main_url}/fees"] = pages[f"{main_url}/fees"].replace("$100", "$120")
        first_report = await scan_website(client, service.get(website.id))
        assert first_report is not None and "$120" in first_report

        pages[f"{main_url}/dates"] = pages[f"{main_url}/dates"].replace("May", "June")
        second_report = await scan_website(client, service.get(website.id))

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

    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url), critical_pages=[fees_url]))

    def fees_page() -> CriticalPageRead:
        return next(page for page in service.get(website.id).critical_pages if page.url == HttpUrl(fees_url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scan_website(client, website) is None  # baseline

        fees_status_code = 500
        assert await scan_website(client, service.get(website.id)) is None  # may be a blip
        report = await scan_website(client, service.get(website.id))
        assert report is not None
        assert "Watched Pages Unreachable (1)" in report
        assert "HTTP 500" in report
        assert await scan_website(client, service.get(website.id)) is None  # not reported again
        assert fees_page().consecutive_failures == 3

        fees_status_code = 200
        assert await scan_website(client, service.get(website.id)) is None

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

    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch(
        "app.backend.change_detection.crawl_site", side_effect=WebConnectionError("Connection timed out")
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=html))
    ) as client:
        assert await scan_website(client, website) is not None  # the connection error is reported

        crawl.side_effect = None
        crawl.return_value = crawled
        assert await scan_website(client, service.get(website.id)) is None

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
    mocker.patch("app.scanning.website_scan.get_website_updates", return_value=website_updates)
    mock_update = mocker.patch.object(WebsiteService, "update", return_value=populated_website)
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")
    mock_generate_report = mocker.patch("app.scanning.website_scan.generate_scan_report_html")

    report = await scan_website(client=mocker.AsyncMock(spec=httpx2.AsyncClient), website=populated_website)

    assert report is None
    assert mock_update.called is (website_updates is not None)  # baselines are still saved
    mock_reset_failed_attempts.assert_called_once_with(populated_website.id)
    mock_generate_report.assert_not_called()


@pytest.mark.anyio
async def test_scan_website_traffic_error_handling(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that TrafficError triggers cooldown handling and returns a traffic error HTML report."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanning.website_scan.get_website_updates",
        side_effect=TrafficError(url=str(populated_website.url), status_code=429),
    )
    mock_handle_traffic = mocker.patch.object(WebsiteService, "handle_traffic_error", return_value="Cooldown applied")
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    report = await scan_website(client=mock_client, website=populated_website)

    mock_handle_traffic.assert_called_once_with(populated_website)
    mock_reset_failed_attempts.assert_not_called()
    assert isinstance(report, str)
    assert "Cooldown applied" in report


@pytest.mark.anyio
async def test_scan_website_traffic_error_re_raised_with_params(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that TrafficError is re-raised when delay or concurrent parameters are provided."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanning.website_scan.get_website_updates",
        side_effect=TrafficError(url=str(populated_website.url), status_code=429),
    )

    with pytest.raises(TrafficError, match="Scan aborted, try increasing delay or reducing concurrent"):
        await scan_website(
            client=mock_client,
            website=populated_website,
            delay=1.0,
        )


@pytest.mark.anyio
async def test_scan_website_connection_error_handling(populated_website: WebsiteRead, mocker: MockerFixture):
    """Tests that WebConnectionError handles unreachable site state and returns a connection error HTML report."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.scanning.website_scan.get_website_updates",
        side_effect=WebConnectionError("Connection timed out"),
    )
    mock_handle_conn = mocker.patch.object(WebsiteService, "handle_connection_error", return_value="Site unreachable")
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    report = await scan_website(client=mock_client, website=populated_website)

    mock_handle_conn.assert_called_once_with(populated_website.id)
    mock_reset_failed_attempts.assert_not_called()
    assert isinstance(report, str)
    assert "Site unreachable" in report


@pytest.mark.anyio
async def test_scan_website_deactivates_a_website_too_large_to_scan(session: Session, mocker: MockerFixture):
    """Tests a website with more pages than the crawler will scan is deactivated, saying why, and its critical
    pages are still checked straight away, without saving anything from the refused crawl."""
    main_url = "https://example.com"
    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch(
        "app.backend.change_detection.crawl_site", side_effect=WebsiteTooLargeError(main_url, max_pages=50_000)
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text="<p>Home page.</p>"))
    ) as client:
        report = await scan_website(client, website)

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
    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scan_website(client, website) is None  # baseline

        html = html.replace("$100", "$120")
        crawl.side_effect = WebsiteTooLargeError(main_url, max_pages=50_000)
        report = await scan_website(client, service.get(website.id))

    assert report is not None
    assert "Website Too Large" in report
    assert "$120" in report
    assert crawl.call_count == 2  # The critical pages were checked again without crawling the website


@pytest.mark.anyio
async def test_inactive_website_only_has_its_critical_pages_scanned(session: Session, mocker: MockerFixture):
    """Tests an inactive website is not crawled, but changes on its critical pages are still found and reported."""
    main_url = "https://example.com"
    html = "<html><body><p>Applications close in May.</p></body></html>"
    mocker.patch("app.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.change_detection.crawl_site")
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))
    service.update(id=website.id, model_update=WebsiteUpdate(active=False))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert await scan_website(client, service.get(website.id)) is None  # baseline

        html = html.replace("May", "June")
        report = await scan_website(client, service.get(website.id))

    assert report is not None
    assert "June" in report
    crawl.assert_not_called()
    saved = service.get(website.id)
    assert saved.active is False
    assert saved.internal_link_count == 0


@pytest.mark.anyio
async def test_cancelling_a_scan_stops_it_and_saves_nothing(
    populated_website: WebsiteRead, mocker: MockerFixture, empty_scan_queue: ScanQueue
):
    """Tests a scan can be cancelled whether it is running or still queued, and nothing from it is saved."""
    crawls_started: list[str] = []
    first_crawl_started = asyncio.Event()

    async def crawl(client: httpx2.AsyncClient, website: WebsiteRead, *args: object) -> None:
        crawls_started.append(str(website.url))
        first_crawl_started.set()
        await asyncio.Event().wait()  # Runs until cancelled

    mocker.patch("app.scanning.website_scan.get_website_updates", side_effect=crawl)
    mock_update = mocker.patch.object(WebsiteService, "update")
    queued_website = populated_website.model_copy(update={"url": "https://queued.com"})
    client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    scans = [asyncio.create_task(scan_website(client, website)) for website in (populated_website, queued_website)]
    await first_crawl_started.wait()

    assert empty_scan_queue.cancel(str(queued_website.url))
    assert empty_scan_queue.cancel(str(populated_website.url))

    for scan in scans:
        with pytest.raises(ScanCancelledError):
            await scan
    assert crawls_started == [str(populated_website.url)]  # The queued scan never started
    mock_update.assert_not_called()
    assert empty_scan_queue.queued_urls == []
    assert not empty_scan_queue.cancel(str(populated_website.url))  # Nothing left to cancel
