import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.schema import DBUser, DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkCreate, InternalLinkRead
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate


def test_get_all_websites(session: Session, test_website: DBWebsite) -> None:
    """
    Tests retrieving a list of all websites.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_websites: Sequence[WebsiteRead] = WebsiteService(session).get_all()
    assert len(fetched_websites) >= 1
    assert any(c.id == test_website.id for c in fetched_websites)
    assert any(c.url == test_website.url for c in fetched_websites)


def test_get_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests retrieving an existing website by ID.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)

    assert fetched_website is not None
    assert fetched_website.id == test_website.id
    assert fetched_website.url == test_website.url


def test_get_website_raises_not_found(session: Session) -> None:
    """
    Tests that retrieving a non-existent website ID raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=uuid.uuid4())


def test_get_website_by_url(session: Session, test_website: DBWebsite) -> None:
    """
    Tests retrieving a website record by its URL.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_website: WebsiteRead = WebsiteService(session).get_by_url(
        url=test_website.url, user_id=test_website.user_id
    )

    assert fetched_website is not None
    assert fetched_website.id == test_website.id
    assert fetched_website.url == test_website.url


def test_get_website_by_url_raises_not_found(session: Session, test_user: DBUser) -> None:
    """
    Tests that retrieving a non-existent URL raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).get_by_url(url="https://www.nonexistent_website.com", user_id=test_user.id)


def test_create_website(session: Session) -> None:
    """
    Tests creating a new website with basic details.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(url="https://www.test_website.com", user_id=uuid.uuid4())

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url


def test_create_website_with_links_and_critical_pages(session: Session) -> None:
    """
    Tests creating a new website with attached critical pages.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(
        url="https://www.test_website.com",
        user_id=uuid.uuid4(),
        critical_pages=["https://www.test_website.com/critical_page"],
    )

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url
    assert fetched_website.critical_pages
    assert len(fetched_website.critical_pages) == 1
    assert fetched_website.critical_pages[0].url == "https://www.test_website.com/critical_page"


def test_update_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests updating an existing website's details.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    model_update = WebsiteUpdate(url="https://www.updated_website.com")

    updated_website: WebsiteRead = WebsiteService(session).update(id=test_website.id, model_update=model_update)
    assert updated_website.id == test_website.id
    assert updated_website.url == model_update.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)
    assert fetched_website.url == model_update.url


def test_update_website_internal_links(session: Session, test_website: DBWebsite) -> None:
    """
    Tests updating a website with added and removed internal links.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    internal_link_service = InternalLinkService(session)
    existing_link_url = f"https://{test_website.url}/link_to_delete"
    internal_link_service.create_batch(urls=[existing_link_url], website_id=test_website.id)

    new_link_url = f"https://{test_website.url}/link_to_add"
    model_update = WebsiteUpdate(
        recent_added_internal_links=[new_link_url],
        recent_removed_internal_links=[existing_link_url],
    )

    updated_website = WebsiteService(session).update(id=test_website.id, model_update=model_update)

    # Confirm added link exists
    added_link = internal_link_service.get_by_url(url=new_link_url)
    assert added_link.website_id == test_website.id

    # Confirm deleted link is gone
    with pytest.raises(NotFoundError):
        internal_link_service.get_by_url(url=existing_link_url)

    # Confirm relationship payload on website read model
    internal_link_urls = [link.url for link in updated_website.internal_links or []]
    assert new_link_url in internal_link_urls
    assert existing_link_url not in internal_link_urls


def test_delete_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests deleting an existing website and cascading its children.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    WebsiteService(session).delete(id=test_website.id)

    # Check the website can no longer be retrieved
    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=test_website.id)


def test_delete_website_raises_not_found(session: Session) -> None:
    """
    Tests that deleting a non-existent website raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).delete(id=uuid.uuid4())


def test_delete_website_cascades(session: Session, test_website: DBWebsite) -> None:
    """
    Tests deleting an existing website and cascading its children.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    # create link and page to be deleted on cascade
    created_internal_link: InternalLinkRead = InternalLinkService(session).create(
        model_create=InternalLinkCreate(
            url=f"https://{test_website.url}/internal_link",
            website_id=test_website.id,
        )
    )
    created_critical_page: CriticalPageRead = CriticalPageService(session).create(
        model_create=CriticalPageCreate(
            url=f"https://{test_website.url}/critical_page",
            website_id=test_website.id,
        )
    )
    WebsiteService(session).delete(id=test_website.id)

    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=test_website.id)

    with pytest.raises(NotFoundError):
        InternalLinkService(session).get(id=created_internal_link.id)

    with pytest.raises(NotFoundError):
        CriticalPageService(session).get(id=created_critical_page.id)


def test_update_website_critical_page_updates(session: Session, test_website: DBWebsite) -> None:
    """
    Tests updating a website's critical pages using critical_page_updates.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    critical_page_service = CriticalPageService(session)
    created_page = critical_page_service.create(
        model_create=CriticalPageCreate(
            url=f"https://{test_website.url}/critical_1",
            website_id=test_website.id,
        )
    )

    updated_url = f"https://{test_website.url}/critical_1_updated"
    model_update = WebsiteUpdate(critical_page_updates={created_page.id: CriticalPageUpdate(url=updated_url)})

    WebsiteService(session).update(id=test_website.id, model_update=model_update)

    fetched_page = critical_page_service.get(id=created_page.id)
    assert fetched_page.url == updated_url
