from sqlalchemy.orm import Session

from app.db.services.website_service import WebsiteService
from app.backend.diff_checker.compare_links import compare_and_update_links


def test_stores_new_website_links(session: Session) -> None:
    website_url = "https://compare-new.example.com"
    links = {
        f"{website_url}/about",
        f"{website_url}/contact",
    }

    changes = compare_and_update_links(
        current_data={website_url: links},
        session=session,
    )

    stored_website = WebsiteService(session).get_by_url(website_url)
    stored_links = {
        internal_link.url
        for internal_link in stored_website.internal_links
    }

    assert stored_links == links
    assert changes[website_url] == {
        "added": sorted(links),
        "removed": [],
    }


def test_adds_and_removes_changed_links(session: Session) -> None:
    website_url = "https://compare-update.example.com"

    old_links = {
        f"{website_url}/about",
        f"{website_url}/old-page",
    }

    compare_and_update_links(
        current_data={website_url: old_links},
        session=session,
    )

    new_links = {
        f"{website_url}/about",
        f"{website_url}/new-page",
    }

    changes = compare_and_update_links(
        current_data={website_url: new_links},
        session=session,
    )

    stored_website = WebsiteService(session).get_by_url(website_url)
    stored_links = {
        internal_link.url
        for internal_link in stored_website.internal_links
    }

    assert stored_links == new_links
    assert changes[website_url] == {
        "added": [f"{website_url}/new-page"],
        "removed": [f"{website_url}/old-page"],
    }


def test_returns_empty_changes_when_links_are_unchanged(
    session: Session,
) -> None:
    website_url = "https://compare-unchanged.example.com"
    links = {
        f"{website_url}/about",
        f"{website_url}/contact",
    }

    compare_and_update_links(
        current_data={website_url: links},
        session=session,
    )

    changes = compare_and_update_links(
        current_data={website_url: links},
        session=session,
    )

    assert changes[website_url] == {
        "added": [],
        "removed": [],
    }