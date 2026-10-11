import uuid
from collections.abc import Sequence

import pytest
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.core.errors import IntegrityError, NotFoundError
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.models.internal_link_models import (
    InternalLinkCreate,
    InternalLinkRead,
    InternalLinkUpdate,
)
from app.models.website_models import WebsiteCreate, WebsiteRead


def test_get_all_internal_links(session: Session, test_internal_link: InternalLinkRead) -> None:
    """
    Tests retrieving a list of all internal_links.

    Args:
        session: The database session fixture.
        test_internal_link: The test critical page record.
    """
    fetched_internal_links: Sequence[InternalLinkRead] = InternalLinkService(session).get_all()
    assert len(fetched_internal_links) >= 1
    assert any(c.id == test_internal_link.id for c in fetched_internal_links)
    assert any(c.url == test_internal_link.url for c in fetched_internal_links)


def test_get_internal_link(session: Session, test_internal_link: InternalLinkRead) -> None:
    """
    Tests retrieving an existing critical page by ID.

    Args:
        session: The database session fixture.
        test_internal_link: The test critical page record.
    """
    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get(id=test_internal_link.id)

    assert fetched_internal_link is not None
    assert fetched_internal_link.id == test_internal_link.id
    assert fetched_internal_link.url == test_internal_link.url


def test_create_internal_link(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests creating a new critical page with basic details.

    Args:
        session: The database session fixture.
    """
    internal_link_details = InternalLinkCreate(
        url=HttpUrl("https://www.test_website.com/test_internal_link"),
        website_id=test_website.id,
    )

    created_internal_link: InternalLinkRead = InternalLinkService(session).create(internal_link_details)
    assert created_internal_link.id is not None
    assert created_internal_link.url == internal_link_details.url

    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get(id=created_internal_link.id)
    assert fetched_internal_link.url == internal_link_details.url


def test_create_batch_internal_links(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests creating multiple internal_links in batch using a shared website ID.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    urls = [
        HttpUrl("https://www.test_website.com/batch_link_1"),
        HttpUrl("https://www.test_website.com/batch_link_2"),
    ]
    InternalLinkService(session).create_batch(urls, test_website.id)

    assert sorted(InternalLinkService(session).get_urls_for_website(test_website.id)) == [str(url) for url in urls]


def test_get_urls_for_website_returns_only_that_websites_links(
    session: Session, test_website: WebsiteRead, test_internal_link: InternalLinkRead
) -> None:
    """
    Tests the URLs of a website's internal links are returned, and not those of other websites.

    Args:
        session: The database session fixture.
        test_website: The test website record.
        test_internal_link: The test website's internal link.
    """
    service = InternalLinkService(session)
    other_website = WebsiteService(session).create(WebsiteCreate(url=HttpUrl("https://other.example.com")))
    service.create_batch([HttpUrl("https://other.example.com/page")], website_id=other_website.id)

    assert service.get_urls_for_website(test_website.id) == [str(test_internal_link.url)]
    assert service.get_urls_for_website(other_website.id) == ["https://other.example.com/page"]


def test_delete_batch_deletes_more_links_than_one_statement_can_hold(
    session: Session, test_website: WebsiteRead
) -> None:
    """
    Tests deleting more links than SQLite allows values in one statement (e.g. a large website losing most of its
    pages), which are deleted a chunk at a time, leaving the website's other links alone.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = InternalLinkService(session)
    removed_urls = [HttpUrl(f"https://www.test_website.com/removed/{number}") for number in range(40_000)]
    kept_url = HttpUrl("https://www.test_website.com/kept")
    service.create_batch([*removed_urls, kept_url], website_id=test_website.id)

    service.delete_batch(urls=removed_urls, website_id=test_website.id)

    assert service.get_urls_for_website(test_website.id) == [str(kept_url)]


def test_update_internal_link(session: Session, test_internal_link: InternalLinkRead) -> None:
    """
    Tests updating an existing internal_link's details.

    Args:
        session: The database session fixture.
        test_internal_link: The test critical page record.
    """
    model_update = InternalLinkUpdate(url=HttpUrl("https://www.test_website.com/updated_internal_link"))

    updated_internal_link: InternalLinkRead = InternalLinkService(session).update(
        id=test_internal_link.id, model_update=model_update
    )
    assert updated_internal_link.id == test_internal_link.id
    assert updated_internal_link.url == model_update.url

    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get(id=test_internal_link.id)
    assert fetched_internal_link.url == model_update.url


def test_delete_internal_link(session: Session, test_internal_link: InternalLinkRead) -> None:
    """
    Tests deleting an existing internal_link.

    Args:
        session: The database session fixture.
        test_internal_link: The test critical page record.
    """
    InternalLinkService(session).delete(id=test_internal_link.id)

    # Assert it can no longer be retrieved
    with pytest.raises(NotFoundError):
        InternalLinkService(session).get(id=test_internal_link.id)


def test_delete_batch_internal_links(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests deleting multiple internal_links in batch.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = InternalLinkService(session)
    urls = [
        HttpUrl("https://www.test_website.com/batch_delete_1"),
        HttpUrl("https://www.test_website.com/batch_delete_2"),
    ]
    service.create_batch(urls, website_id=test_website.id)
    assert len(service.get_urls_for_website(test_website.id)) == len(urls)

    service.delete_batch(urls=urls, website_id=test_website.id)

    assert service.get_urls_for_website(test_website.id) == []


def test_delete_batch_empty_urls(
    session: Session, test_website: WebsiteRead, test_internal_link: InternalLinkRead
) -> None:
    """
    Tests that calling delete_batch with an empty sequence executes without error and deletes nothing.

    Args:
        session: The database session fixture.
    """
    InternalLinkService(session).delete_batch(urls=[], website_id=test_website.id)

    assert InternalLinkService(session).get_urls_for_website(test_website.id) == [str(test_internal_link.url)]


def test_get_internal_link_raises_not_found(session: Session) -> None:
    """
    Tests that retrieving a non-existent internal_link by ID raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        InternalLinkService(session).get(id=uuid.uuid4())


def test_update_internal_link_raises_not_found(session: Session) -> None:
    """
    Tests that updating a non-existent internal_link raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    model_update = InternalLinkUpdate(url=HttpUrl("https://www.test_website.com/updated_link"))
    with pytest.raises(NotFoundError):
        InternalLinkService(session).update(id=uuid.uuid4(), model_update=model_update)


def test_delete_internal_link_raises_not_found(session: Session) -> None:
    """
    Tests that deleting a non-existent internal_link raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        InternalLinkService(session).delete(id=uuid.uuid4())


def test_two_websites_can_save_the_same_page(session: Session, test_website: WebsiteRead) -> None:
    """Tests overlapping websites (e.g. example.com and example.com/research) can both save a page they share, so
    the second website's crawl does not fail."""
    research_website = WebsiteService(session).create(WebsiteCreate(url=HttpUrl(f"{test_website.url}research")))
    shared_page = HttpUrl(f"{test_website.url}research/projects")

    InternalLinkService(session).create_batch([shared_page], website_id=test_website.id)
    InternalLinkService(session).create_batch([shared_page], website_id=research_website.id)

    assert InternalLinkService(session).get_urls_for_website(test_website.id) == [str(shared_page)]
    assert InternalLinkService(session).get_urls_for_website(research_website.id) == [str(shared_page)]


@pytest.fixture
def overlapping_website(session: Session, test_website: WebsiteRead) -> WebsiteRead:
    """Provides a second website that shares a page with the test website, which both have saved."""
    research_website = WebsiteService(session).create(WebsiteCreate(url=HttpUrl(f"{test_website.url}research")))
    for website in (test_website, research_website):
        InternalLinkService(session).create_batch([HttpUrl(f"{test_website.url}research/projects")], website.id)
    return research_website


def test_delete_batch_only_deletes_the_given_websites_links(
    session: Session, test_website: WebsiteRead, overlapping_website: WebsiteRead
) -> None:
    """Tests deleting a page from one website leaves the same page saved for another website."""
    shared_page = HttpUrl(f"{test_website.url}research/projects")

    InternalLinkService(session).delete_batch(urls=[shared_page], website_id=test_website.id)

    assert InternalLinkService(session).get_urls_for_website(test_website.id) == []
    assert InternalLinkService(session).get_urls_for_website(overlapping_website.id) == [str(shared_page)]


def test_delete_all_for_website_only_deletes_that_websites_links(
    session: Session, test_website: WebsiteRead, overlapping_website: WebsiteRead
) -> None:
    """Tests every link of one website is deleted, and another website's links are kept."""
    InternalLinkService(session).delete_all_for_website(overlapping_website.id)

    assert InternalLinkService(session).get_urls_for_website(overlapping_website.id) == []
    assert InternalLinkService(session).get_urls_for_website(test_website.id) == [
        f"{test_website.url}research/projects"
    ]


def test_create_batch_with_no_urls_saves_nothing(session: Session, test_website: WebsiteRead) -> None:
    """Tests saving an empty list of links (e.g. a scan that found no new pages) does nothing, rather than failing."""
    InternalLinkService(session).create_batch([], website_id=test_website.id)

    assert InternalLinkService(session).get_urls_for_website(test_website.id) == []


def test_a_website_cannot_save_the_same_page_twice(session: Session, test_internal_link: InternalLinkRead) -> None:
    """Tests a page already saved for a website cannot be saved for it again, as links are unique per website."""
    with pytest.raises(IntegrityError):
        InternalLinkService(session).create_batch([test_internal_link.url], website_id=test_internal_link.website_id)


def test_links_of_a_website_that_does_not_exist_are_refused(session: Session) -> None:
    """Tests links cannot be saved for a website that does not exist, as foreign keys are checked."""
    with pytest.raises(IntegrityError):
        InternalLinkService(session).create_batch([HttpUrl("https://example.com/a")], website_id=uuid.uuid4())
