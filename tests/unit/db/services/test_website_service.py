from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.db.errors import NotFoundError
from app.db.models.critical_page_models import CriticalPageBase, CriticalPageCreate, CriticalPageRead
from app.db.models.internal_link_models import InternalLinkCreate, InternalLinkRead
from app.db.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService


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


def test_create_website(session: Session) -> None:
    """
    Tests creating a new website with basic details.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(url="https://www.test_website.com")

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url


def test_create_website_with_links_and_critical_pages(session: Session) -> None:
    """
    Tests creating a new website with basic details.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(
        url="https://www.test_website.com",
        critical_pages=[CriticalPageBase(url="https://www.test_website.com/critical_page")],
        internal_links=["https://www.test_website.com/internal_link"],
    )

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url


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
