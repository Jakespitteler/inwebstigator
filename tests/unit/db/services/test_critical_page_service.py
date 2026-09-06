from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.db.errors import NotFoundError
from app.db.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.db.schema import DBCriticalPage, DBWebsite
from app.db.services.critical_page_service import CriticalPageService


def test_get_all_critical_pages(session: Session, test_critical_page: DBCriticalPage) -> None:
    """
    Tests retrieving a list of all critical_pages.

    Args:
        session: The database session fixture.
        test_critical_page: The test critical page record.
    """
    fetched_critical_pages: Sequence[CriticalPageRead] = CriticalPageService(session).get_all()
    assert len(fetched_critical_pages) >= 1
    assert any(c.id == test_critical_page.id for c in fetched_critical_pages)
    assert any(c.url == test_critical_page.url for c in fetched_critical_pages)


def test_get_critical_page(session: Session, test_critical_page: DBCriticalPage) -> None:
    """
    Tests retrieving an existing critical page by ID.

    Args:
        session: The database session fixture.
        test_critical_page: The test critical page record.
    """
    fetched_critical_page: CriticalPageRead = CriticalPageService(session).get(id=test_critical_page.id)

    assert fetched_critical_page is not None
    assert fetched_critical_page.id == test_critical_page.id
    assert fetched_critical_page.url == test_critical_page.url


def test_create_critical_page(session: Session, test_website: DBWebsite) -> None:
    """
    Tests creating a new critical page with basic details.

    Args:
        session: The database session fixture.
    """
    critical_page_details = CriticalPageCreate(
        url="https://www.test_website.com/test_critical_page",
        links=[],
        documents=[],
        text_body="",
        website_id=test_website.id,
    )

    created_critical_page: CriticalPageRead = CriticalPageService(session).create(critical_page_details)
    assert created_critical_page.id is not None
    assert created_critical_page.url == critical_page_details.url

    fetched_critical_page: CriticalPageRead = CriticalPageService(session).get(id=created_critical_page.id)
    assert fetched_critical_page.url == critical_page_details.url


def test_update_critical_page(session: Session, test_critical_page: DBCriticalPage) -> None:
    """
    Tests updating an existing critical_page's details.

    Args:
        session: The database session fixture.
        test_critical_page: The test critical page record.
    """
    model_update = CriticalPageUpdate(links=["https://www.test_website.com/updated_critical_page_link"])

    updated_critical_page: CriticalPageRead = CriticalPageService(session).update(
        id=test_critical_page.id, model_update=model_update
    )
    assert updated_critical_page.id == test_critical_page.id
    assert updated_critical_page.links == model_update.links

    fetched_critical_page: CriticalPageRead = CriticalPageService(session).get(id=test_critical_page.id)
    assert fetched_critical_page.links == model_update.links


def test_delete_critical_page(session: Session, test_critical_page: DBCriticalPage) -> None:
    """
    Tests deleting an existing critical_page.

    Args:
        session: The database session fixture.
        test_critical_page: The test critical page record.
    """
    CriticalPageService(session).delete(id=test_critical_page.id)

    # Assert it can no longer be retrieved
    with pytest.raises(NotFoundError):
        CriticalPageService(session).get(id=test_critical_page.id)
