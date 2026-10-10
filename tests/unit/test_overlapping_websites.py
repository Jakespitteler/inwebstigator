import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import IntegrityError, WebsiteAlreadyMonitoredError
from app.core.urls import page_key
from app.db.schema import DBInternalLink, DBWebsite
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from tests.unit.backend.email_service.builders import make_scan_run

# ======================================
# Websites that overlap can share internal links
# ======================================


def test_overlapping_websites_can_both_save_the_same_internal_link(session: Session) -> None:
    """Tests a website inside another (e.g. uwa.edu.au/news inside uwa.edu.au) can save the links both crawls find.

    Links used to be unique across every website, so the second website's scan failed every time.
    """
    service = WebsiteService(session)
    whole_site = service.create(WebsiteCreate(url=HttpUrl("https://example.com")))
    news_section = service.create(WebsiteCreate(url=HttpUrl("https://example.com/news")))
    shared_link = HttpUrl("https://example.com/news/story")

    service.update(whole_site.id, WebsiteUpdate(recent_added_internal_links=[shared_link]))
    service.update(news_section.id, WebsiteUpdate(recent_added_internal_links=[shared_link]))

    saved = session.scalars(select(DBInternalLink).where(DBInternalLink.url == str(shared_link))).all()
    assert {link.website_id for link in saved} == {whole_site.id, news_section.id}


def test_an_internal_link_is_still_unique_within_its_website(session: Session, test_website: WebsiteRead) -> None:
    """Tests one website still cannot save the same link twice."""
    service = InternalLinkService(session)
    service.create_batch([HttpUrl("https://www.test_website.com/page")], website_id=test_website.id)

    with pytest.raises(IntegrityError):
        service.create_batch([HttpUrl("https://www.test_website.com/page")], website_id=test_website.id)


def test_deleting_an_overlapping_websites_link_leaves_the_other_websites_copy(session: Session) -> None:
    """Tests a link that disappears from one website's crawl is only removed from that website."""
    service = WebsiteService(session)
    whole_site = service.create(WebsiteCreate(url=HttpUrl("https://example.com")))
    news_section = service.create(WebsiteCreate(url=HttpUrl("https://example.com/news")))
    shared_link = HttpUrl("https://example.com/news/story")
    service.update(whole_site.id, WebsiteUpdate(recent_added_internal_links=[shared_link]))
    service.update(news_section.id, WebsiteUpdate(recent_added_internal_links=[shared_link]))

    service.update(news_section.id, WebsiteUpdate(recent_removed_internal_links=[shared_link]))

    link_service = InternalLinkService(session)
    assert link_service.get_urls_for_website(whole_site.id) == [str(shared_link)]
    assert link_service.get_urls_for_website(news_section.id) == []


# ======================================
# The same website written differently is not added twice
# ======================================


@pytest.mark.parametrize(
    "written_differently",
    [
        "https://www.test_website.com",
        "https://www.test_website.com/",
        "http://www.test_website.com/",
        "https://test_website.com",
        "https://WWW.Test_Website.COM/",
        "https://www.test_website.com/#top",
    ],
)
def test_page_key_matches_however_the_address_is_written(written_differently: str) -> None:
    """Tests addresses for the same page match with or without "www.", "/" or a #section, in http or https."""
    assert page_key(written_differently) == page_key("https://www.test_website.com/")


@pytest.mark.parametrize(
    "different_page",
    [
        "https://www.test_website.com/news",
        "https://news.test_website.com/",
        "https://www.other_website.com/",
        "https://www.test_website.com/?page=2",
    ],
)
def test_page_key_tells_different_pages_apart(different_page: str) -> None:
    """Tests a different path, subdomain, website or query is a different page."""
    assert page_key(different_page) != page_key("https://www.test_website.com/")


def test_get_by_url_finds_a_website_however_its_address_is_written(session: Session, test_website: WebsiteRead) -> None:
    """Tests looking up a website finds it even when its address is written differently to how it was saved."""
    assert WebsiteService(session).get_by_url(HttpUrl("http://test_website.com")).id == test_website.id


def test_adding_a_website_already_monitored_is_refused(session: Session, test_website: WebsiteRead) -> None:
    """Tests the same website written differently is refused, and no copy of it is saved."""
    with pytest.raises(WebsiteAlreadyMonitoredError, match="already being monitored"):
        WebsiteService(session).create(WebsiteCreate(url=HttpUrl("https://test_website.com")))

    assert len(session.scalars(select(DBWebsite)).all()) == 1


def test_a_section_of_a_monitored_website_can_still_be_added(session: Session, test_website: WebsiteRead) -> None:
    """Tests a website inside one already monitored is a different website, so it can be added."""
    news_section = WebsiteService(session).create(WebsiteCreate(url=HttpUrl("https://www.test_website.com/news")))

    assert news_section.id != test_website.id


# ======================================
# Through the dashboard's routes
# ======================================


def test_dashboard_says_when_a_website_is_already_monitored(
    api_client: TestClient, session: Session, test_website: WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests adding a website already monitored, written with http rather than https, gets a 409 the dashboard
    shows, and saves no copy."""
    mocker.patch("app.backend.websites.website_setup.check_pages_exist", return_value=None)

    response = api_client.post("/scanner/initial_scan", json={"url": "http://test_website.com"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == f"{test_website.url} is already being monitored."
    assert len(session.scalars(select(DBWebsite)).all()) == 1


def test_run_scan_finds_a_website_saved_with_its_address_written_differently(
    api_client: TestClient, session: Session, test_website: WebsiteRead, mocker: MockerFixture
) -> None:
    """Tests "Run Scan Now" scans the website already saved rather than creating a copy of it."""
    mock_scan_website = mocker.patch("app.backend.scanning.manual_scan.scan_website", return_value=make_scan_run())

    response = api_client.post("/scanner/run", data={"url": "https://test_website.com"})

    assert response.status_code == 200, response.text
    assert mock_scan_website.call_args.args[1].id == test_website.id
    assert len(session.scalars(select(DBWebsite)).all()) == 1
