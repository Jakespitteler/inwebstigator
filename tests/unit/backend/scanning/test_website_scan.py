import asyncio
import uuid
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime

import httpx2
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from app.backend.email_service.html_bodies import generate_scan_report_html
from app.backend.scanning.change_detection import CrawlFailedError
from app.backend.scanning.scan_queue import ScanQueue
from app.backend.scanning.website_scan import scan_website
from app.core.config import config
from app.core.errors import ScanCancelledError, TrafficError, WebConnectionError, WebsiteTooLargeError
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.scan_run_models import ChangeKind, ScanRunRead, ScanStatus
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead, WebsiteUpdate


def _report(scan_run: ScanRunRead) -> str:
    """Writes a scan's report the way its recipients would be emailed it."""
    return generate_scan_report_html("https://example.com", scan_run)


@pytest.mark.anyio
async def test_main_url_content_is_scanned_and_shown_in_updates(
    session: Session, mocker: MockerFixture, api_client: TestClient
):
    """Scan the starting page without manually adding it, then detect a real content change."""
    main_url = "https://example.com/au?edition=local"
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value=set())
    requested_urls: list[str] = []
    html = "<html><body><p>Original main page content.</p></body></html>"

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert not (await scan_website(client, website)).has_report  # baseline
        baseline = service.get(website.id)
        assert len(baseline.critical_pages) == 1
        main_page = baseline.critical_pages[0]
        assert main_page.url == HttpUrl(main_url)
        assert main_page.text_body == html

        html = html.replace("</body>", "<p>New main-page announcement.</p></body>")
        scan_run = await scan_website(client, baseline)
        updated = service.get(website.id)
        assert updated.critical_pages[0].id == main_page.id
        assert updated.critical_pages[0].text_body == html
        assert scan_run.has_report
        assert "New main-page announcement." in _report(scan_run)

        response = api_client.get("/")
        assert response.status_code == 200
        dashboard = BeautifulSoup(response.text, "html.parser")

        updates_panel_tag = dashboard.select_one("#updates-panel")
        assert updates_panel_tag
        assert "New main-page announcement." in updates_panel_tag.get_text()

        website_panel_tag = dashboard.select_one("#websites-panel")
        assert website_panel_tag
        assert "Main website (automatic)" in website_panel_tag.get_text()

        assert not (await scan_website(client, updated)).has_report

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

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.websites.website_setup.new_http_client", side_effect=mock_client)
    crawl = mocker.patch(
        "app.backend.scanning.change_detection.crawl_site", return_value={main_url, f"{main_url}/about"}
    )

    response = api_client.post("/scanner/initial_scan", json={"url": main_url})
    assert response.status_code == 200, response.text

    dashboard = BeautifulSoup(api_client.get("/").text, "html.parser")
    updates_panel_tag = dashboard.select_one("#updates-panel")
    assert updates_panel_tag
    updates_panel = " ".join(updates_panel_tag.get_text().split())
    assert "No changes in its only scan" in updates_panel
    assert "The fee is $100." not in updates_panel

    # Next scan: the page text changes and a new internal page appears
    html = html.replace("$100", "$120")
    crawl.return_value = {main_url, f"{main_url}/about", f"{main_url}/new-page"}
    website = WebsiteService(session).get_by_url(HttpUrl(main_url))
    async with mock_client() as client:
        report = _report(await scan_website(client, website))

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

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(
        WebsiteCreate(url=HttpUrl(main_url), critical_pages=[f"{main_url}/fees", f"{main_url}/dates"])
    )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert not (await scan_website(client, website)).has_report  # baseline

        pages[f"{main_url}/fees"] = pages[f"{main_url}/fees"].replace("$100", "$120")
        first_report = _report(await scan_website(client, service.get(website.id)))
        assert "$120" in first_report

        pages[f"{main_url}/dates"] = pages[f"{main_url}/dates"].replace("May", "June")
        second_report = _report(await scan_website(client, service.get(website.id)))

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

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url), critical_pages=[fees_url]))

    def fees_page() -> CriticalPageRead:
        return next(page for page in service.get(website.id).critical_pages if page.url == HttpUrl(fees_url))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert not (await scan_website(client, website)).has_report  # baseline

        fees_status_code = 500
        assert not (await scan_website(client, service.get(website.id))).has_report  # may be a blip
        report = _report(await scan_website(client, service.get(website.id)))
        assert "Watched Pages Unreachable (1)" in report
        assert "HTTP 500" in report
        assert not (await scan_website(client, service.get(website.id))).has_report  # not reported again
        assert fees_page().consecutive_failures == 3

        fees_status_code = 200
        assert not (await scan_website(client, service.get(website.id))).has_report

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

    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch(
        "app.backend.scanning.change_detection.crawl_site", side_effect=WebConnectionError("Connection timed out")
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=html))
    ) as client:
        assert (await scan_website(client, website)).has_report  # the connection error is reported

        crawl.side_effect = None
        crawl.return_value = crawled
        assert not (await scan_website(client, service.get(website.id))).has_report

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
async def test_scan_website_without_changes_has_no_report(
    session: Session, populated_website: WebsiteRead, mocker: MockerFixture, website_updates: WebsiteUpdate | None
):
    """Tests a scan that finds nothing, or only saves baselines, has no report but is still recorded in the website's
    history, and still clears past failures."""
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.website_scan.get_website_updates", return_value=website_updates)
    mock_update = mocker.patch.object(WebsiteService, "update", return_value=populated_website)
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    scan_run = await scan_website(client=mocker.AsyncMock(spec=httpx2.AsyncClient), website=populated_website)

    assert not scan_run.has_report
    assert scan_run.status is ScanStatus.SUCCESS
    assert ScanRunService(session).get_latest_for_website(populated_website.id, limit=10) == [scan_run]
    assert mock_update.called is (website_updates is not None)  # baselines are still saved
    mock_reset_failed_attempts.assert_called_once_with(populated_website.id)


@pytest.mark.anyio
async def test_scan_website_traffic_error_handling(
    session: Session, populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests that TrafficError triggers cooldown handling and records the scan as rate limited, saying what was done."""
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.backend.scanning.website_scan.get_website_updates",
        side_effect=CrawlFailedError(TrafficError(url=str(populated_website.url), status_code=429), None),
    )
    mock_handle_traffic = mocker.patch.object(WebsiteService, "handle_traffic_error", return_value="Cooldown applied")
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    scan_run = await scan_website(client=mock_client, website=populated_website)

    mock_handle_traffic.assert_called_once_with(populated_website)
    mock_reset_failed_attempts.assert_not_called()
    assert scan_run.status is ScanStatus.TRAFFIC_ERROR
    assert scan_run.message == "Cooldown applied"
    assert scan_run.is_awaiting_email


@pytest.mark.anyio
@pytest.mark.parametrize(("delay", "concurrent"), [(1.0, None), (None, 2)], ids=["own-delay", "own-concurrency"])
async def test_scan_website_traffic_error_re_raised_with_params(
    populated_website: WebsiteRead, mocker: MockerFixture, delay: float | None, concurrent: int | None
):
    """Tests that TrafficError is re-raised when delay or concurrent parameters are provided."""
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.backend.scanning.website_scan.get_website_updates",
        side_effect=CrawlFailedError(TrafficError(url=str(populated_website.url), status_code=429), None),
    )

    with pytest.raises(TrafficError, match="Scan aborted, try increasing delay or reducing concurrent"):
        await scan_website(
            client=mock_client,
            website=populated_website,
            delay=delay,
            concurrent=concurrent,
        )


@pytest.mark.anyio
async def test_scan_website_connection_error_handling(
    session: Session, populated_website: WebsiteRead, mocker: MockerFixture
):
    """Tests that WebConnectionError handles unreachable site state and records the scan as a connection failure."""
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mock_client = mocker.AsyncMock(spec=httpx2.AsyncClient)
    mocker.patch(
        "app.backend.scanning.website_scan.get_website_updates",
        side_effect=CrawlFailedError(WebConnectionError("Connection timed out"), None),
    )
    mock_handle_conn = mocker.patch.object(WebsiteService, "handle_connection_error", return_value="Site unreachable")
    mock_reset_failed_attempts = mocker.patch.object(WebsiteService, "reset_failed_attempts")

    scan_run = await scan_website(client=mock_client, website=populated_website)

    mock_handle_conn.assert_called_once_with(populated_website.id)
    mock_reset_failed_attempts.assert_not_called()
    assert scan_run.status is ScanStatus.CONNECTION_ERROR
    assert "Site unreachable" in _report(scan_run)


@pytest.mark.anyio
async def test_scan_website_deactivates_a_website_too_large_to_scan(session: Session, mocker: MockerFixture):
    """Tests a website with more pages than the crawler will scan is deactivated, saying why, and its critical
    pages are still checked straight away, without saving anything from the refused crawl."""
    main_url = "https://example.com"
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch(
        "app.backend.scanning.change_detection.crawl_site", side_effect=WebsiteTooLargeError(main_url, max_pages=50_000)
    )
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text="<p>Home page.</p>"))
    ) as client:
        scan_run = await scan_website(client, website)

    report = _report(scan_run)
    assert scan_run.status is ScanStatus.TOO_LARGE
    assert "Website Too Large" in report
    assert "more than 50,000 pages" in report
    assert "critical pages are still checked" in report
    assert ScanRunService(session).get_latest_for_website(website.id, limit=10) == [scan_run]  # Recorded as one scan

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
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert not (await scan_website(client, website)).has_report  # baseline

        html = html.replace("$100", "$120")
        crawl.side_effect = WebsiteTooLargeError(main_url, max_pages=50_000)
        report = _report(await scan_website(client, service.get(website.id)))

    assert "Website Too Large" in report
    assert "$120" in report
    assert crawl.call_count == 2  # The critical pages were checked again without crawling the website


@pytest.mark.anyio
async def test_inactive_website_only_has_its_critical_pages_scanned(session: Session, mocker: MockerFixture):
    """Tests an inactive website is not crawled, but changes on its critical pages are still found and reported."""
    main_url = "https://example.com"
    html = "<html><body><p>Applications close in May.</p></body></html>"
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site")
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))
    service.update(id=website.id, model_update=WebsiteUpdate(active=False))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        assert not (await scan_website(client, service.get(website.id))).has_report  # baseline

        html = html.replace("May", "June")
        report = _report(await scan_website(client, service.get(website.id)))

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

    mocker.patch("app.backend.scanning.website_scan.get_website_updates", side_effect=crawl)
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


@pytest.mark.anyio
async def test_each_scan_is_kept_in_the_websites_history_with_what_it_found(session: Session, mocker: MockerFixture):
    """Tests every scan is added to the website's history, with this scan's changes only, and that only the most
    recent scans are kept, so changes found earlier the same day are not overwritten by later scans."""
    main_url = "https://example.com"
    html = "<html><body><h2>Fees</h2><p>The fee is $100.</p></body></html>"
    mocker.patch.object(config, "scans_kept_per_website", 3)
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await scan_website(client, website)  # baseline
        html = html.replace("$100", "$120")
        await scan_website(client, service.get(website.id))
        crawl.return_value = {main_url, f"{main_url}/new-page"}
        await scan_website(client, service.get(website.id))
        await scan_website(client, service.get(website.id))  # nothing changed

    history: list[ScanRunRead] = ScanRunService(session).get_latest_for_website(website.id, limit=10)
    assert [[change.kind for change in scan_run.changes] for scan_run in history] == [
        [],
        [ChangeKind.INTERNAL_LINK_ADDED],
        [ChangeKind.TEXT_CHANGED],
    ]  # The baseline scan is the oldest, so it is the one deleted
    assert history[1].internal_links_added == [HttpUrl(f"{main_url}/new-page")]
    assert history[2].pages[0].text_changed[0].new_block.text == "The fee is $120."


@pytest.mark.anyio
async def test_a_home_page_that_fails_is_reported_as_unreachable_not_as_every_page_removed(
    session: Session, mocker: MockerFixture
):
    """Tests a website whose home page starts failing keeps its saved pages, and its recipients are told the website
    could not be scanned rather than that every page was removed."""
    main_url = "https://example.com"
    pages = {main_url, f"{main_url}/about", f"{main_url}/news"}
    status_code = 200
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        links = "".join(f'<a href="{page}">Page</a>' for page in pages)
        return httpx2.Response(status_code, text=f"<html><body><p>Welcome.</p>{links}</body></html>")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await scan_website(client, website)  # baseline
        status_code = 403
        scan_run = await scan_website(client, service.get(website.id))

    assert scan_run.status is ScanStatus.CONNECTION_ERROR
    assert scan_run.message is not None and "could not be loaded" in scan_run.message
    assert scan_run.internal_links_removed == []
    assert len(InternalLinkService(session).get_urls_for_website(website.id)) == 3


@pytest.mark.anyio
async def test_pages_missing_from_a_partly_down_website_are_only_reported_once_they_stay_missing(
    session: Session, mocker: MockerFixture
):
    """Tests a crawl that cannot find most of a website's pages is treated as the website being partly down, with
    nothing saved, until it has happened enough scans in a row to be real, when the pages are reported as removed."""
    main_url = "https://example.com"
    every_page = {main_url, *(f"{main_url}/page-{number}" for number in range(29))}
    crawled = every_page
    mocker.patch.object(config, "web_crawler_missing_pages_scans_before_accepting", 2)
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))

    def crawl(**_: object) -> set[str]:
        return crawled

    mocker.patch("app.backend.scanning.change_detection.crawl_site", side_effect=crawl)
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text="<p>Home page.</p>"))
    ) as client:
        await scan_website(client, website)  # baseline
        crawled = {main_url, f"{main_url}/page-0"}  # 28 of 30 pages are missing
        first, second = [(await scan_website(client, service.get(website.id))) for _ in range(2)]
        accepted = await scan_website(client, service.get(website.id))

    assert [first.status, second.status] == [ScanStatus.PAGES_MISSING, ScanStatus.PAGES_MISSING]
    assert first.message is not None and "28 of the 30 pages" in first.message
    assert accepted.status is ScanStatus.SUCCESS
    assert len(accepted.internal_links_removed) == 28
    assert len(InternalLinkService(session).get_urls_for_website(website.id)) == 2


@pytest.mark.anyio
async def test_a_small_website_can_lose_most_of_its_pages(session: Session, mocker: MockerFixture):
    """Tests a website with only a few pages has removed pages reported straight away, as losing most of them is
    normal for a small website."""
    main_url = "https://example.com"
    crawled = {main_url, f"{main_url}/a", f"{main_url}/b", f"{main_url}/c"}
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))

    def crawl(**_: object) -> set[str]:
        return crawled

    mocker.patch("app.backend.scanning.change_detection.crawl_site", side_effect=crawl)
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text="<p>Home page.</p>"))
    ) as client:
        await scan_website(client, website)  # baseline
        crawled = {main_url}
        scan_run = await scan_website(client, service.get(website.id))

    assert scan_run.status is ScanStatus.SUCCESS
    assert len(scan_run.internal_links_removed) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("crawl_error", "status", "is_throttled"),
    [
        (TrafficError("https://example.com", status_code=429), ScanStatus.TRAFFIC_ERROR, True),
        (WebConnectionError("https://example.com"), ScanStatus.CONNECTION_ERROR, False),
    ],
    ids=["rate-limited", "unreachable"],
)
async def test_a_website_that_rate_limits_or_cannot_be_reached_is_put_on_cooldown_and_its_page_changes_kept(
    session: Session, mocker: MockerFixture, crawl_error: Exception, status: ScanStatus, is_throttled: bool
) -> None:
    """Tests a scan whose crawl is stopped by the website rate limiting the crawler (which also slows the crawler down
    for it) or being unreachable puts the website on cooldown, but still reports and saves the change its critical
    pages' check found before the crawl failed, rather than leaving it until a scan's crawl succeeds."""
    main_url = "https://example.com"
    old_html = "<html><body><p>The fee is $100.</p></body></html>"
    html = old_html
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: nullcontext(session))
    crawl = mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await scan_website(client, website)  # baseline
        html = html.replace("$100", "$120")
        crawl.side_effect = crawl_error
        scan_run = await scan_website(client, service.get(website.id))

    saved = service.get(website.id)
    assert scan_run.status is status
    assert [change.kind for change in scan_run.changes] == [ChangeKind.TEXT_CHANGED]
    assert saved.on_cooldown_until is not None and saved.on_cooldown_until > datetime.now(UTC)
    crawl_speed = (saved.recommended_delay, saved.recommended_concurrent)
    assert (crawl_speed != (website.recommended_delay, website.recommended_concurrent)) is is_throttled
    assert saved.critical_pages[0].text_body == html


@contextmanager
def _savepoint(session: Session) -> Generator[Session]:
    """Runs one unit of the backend's work in a savepoint of the test's session, undoing all of it if any of it
    fails, the way the app's own `db_context` rolls back its transaction."""
    with session.begin_nested():
        yield session


@pytest.mark.anyio
async def test_a_change_is_not_saved_as_the_new_baseline_unless_its_scan_is_recorded(
    session: Session, mocker: MockerFixture
) -> None:
    """Tests what a scan found and the scan's record are saved together, so a scan that cannot be recorded does not
    save its change as the page's new baseline either, and the next scan still reports the change."""
    main_url = "https://example.com"
    old_html = "<html><body><p>The fee is $100.</p></body></html>"
    html = old_html
    mocker.patch("app.backend.scanning.website_scan.db_context", side_effect=lambda: _savepoint(session))
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={main_url})
    service = WebsiteService(session)
    website = service.create(WebsiteCreate(url=HttpUrl(main_url)))

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=html)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await scan_website(client, website)  # baseline
        html = html.replace("$100", "$120")
        failing_record = mocker.patch.object(ScanRunService, "create", side_effect=RuntimeError("Database is locked"))
        with pytest.raises(RuntimeError, match="Database is locked"):
            await scan_website(client, service.get(website.id))
        assert service.get(website.id).critical_pages[0].text_body == old_html

        mocker.stop(failing_record)
        report = _report(await scan_website(client, service.get(website.id)))

    assert "$120" in report
