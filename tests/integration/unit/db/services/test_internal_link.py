import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.schema import DBInternalLink, DBWebsite
from app.db.services.internal_link_service import InternalLinkService
from app.models.internal_link_models import (
    InternalLinkCreate,
    InternalLinkRead,
    InternalLinkUpdate,
)


def test_get_all_internal_links(session: Session, test_internal_link: DBInternalLink) -> None:
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


def test_get_internal_link(session: Session, test_internal_link: DBInternalLink) -> None:
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


def test_get_internal_link_by_url(session: Session, test_internal_link: DBInternalLink) -> None:
    """
    Tests retrieving an existing internal_link by its URL.

    Args:
        session: The database session fixture.
        test_internal_link: The test internal link record.
    """
    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get_by_url(url=test_internal_link.url)

    assert fetched_internal_link is not None
    assert fetched_internal_link.id == test_internal_link.id
    assert fetched_internal_link.url == test_internal_link.url


def test_get_internal_link_by_url_not_found(session: Session) -> None:
    """
    Tests that retrieving a non-existent internal_link by URL raises a NotFoundError.

    Args:
        session: The database session fixture.
    """
    non_existent_url = "https://www.test_website.com/non-existent-link"

    with pytest.raises(NotFoundError):
        InternalLinkService(session).get_by_url(url=non_existent_url)


def test_create_internal_link(session: Session, test_website: DBWebsite) -> None:
    """
    Tests creating a new critical page with basic details.

    Args:
        session: The database session fixture.
    """
    internal_link_details = InternalLinkCreate(
        url="https://www.test_website.com/test_internal_link",
        website_id=test_website.id,
    )

    created_internal_link: InternalLinkRead = InternalLinkService(session).create(internal_link_details)
    assert created_internal_link.id is not None
    assert created_internal_link.url == internal_link_details.url

    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get(id=created_internal_link.id)
    assert fetched_internal_link.url == internal_link_details.url


def test_create_batch_internal_links(session: Session, test_website: DBWebsite) -> None:
    """
    Tests creating multiple internal_links in batch using a shared website ID.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    urls = [
        "https://www.test_website.com/batch_link_1",
        "https://www.test_website.com/batch_link_2",
    ]
    created_links: Sequence[InternalLinkRead] = InternalLinkService(session).create_batch(urls, test_website.id)

    assert len(created_links) == 2
    for created_link, url in zip(created_links, urls, strict=True):
        assert created_link.id is not None
        assert created_link.url == url
        assert created_link.website_id == test_website.id

        fetched_link = InternalLinkService(session).get(id=created_link.id)
        assert fetched_link.url == url


def test_update_internal_link(session: Session, test_internal_link: DBInternalLink) -> None:
    """
    Tests updating an existing internal_link's details.

    Args:
        session: The database session fixture.
        test_internal_link: The test critical page record.
    """
    model_update = InternalLinkUpdate(url="https://www.test_website.com/updated_internal_link")

    updated_internal_link: InternalLinkRead = InternalLinkService(session).update(
        id=test_internal_link.id, model_update=model_update
    )
    assert updated_internal_link.id == test_internal_link.id
    assert updated_internal_link.url == model_update.url

    fetched_internal_link: InternalLinkRead = InternalLinkService(session).get(id=test_internal_link.id)
    assert fetched_internal_link.url == model_update.url


def test_delete_internal_link(session: Session, test_internal_link: DBInternalLink) -> None:
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


def test_delete_batch_internal_links(session: Session, test_website: DBWebsite) -> None:
    """
    Tests deleting multiple internal_links in batch.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = InternalLinkService(session)
    urls = [
        "https://www.test_website.com/batch_delete_1",
        "https://www.test_website.com/batch_delete_2",
    ]
    service.create_batch(urls, website_id=test_website.id)

    for url in urls:
        service.get_by_url(url)

    service.delete_batch(urls=urls, website_id=test_website.id)

    for url in urls:
        with pytest.raises(NotFoundError):
            service.get_by_url(url)


def test_delete_batch_empty_urls(session: Session, test_website: DBWebsite) -> None:
    """
    Tests that calling delete_batch with an empty sequence executes without error.

    Args:
        session: The database session fixture.
    """
    InternalLinkService(session).delete_batch(urls=[], website_id=test_website.id)


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
    model_update = InternalLinkUpdate(url="https://www.test_website.com/updated_link")
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
