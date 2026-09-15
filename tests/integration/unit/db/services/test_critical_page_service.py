from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.backend.utils.html_parser import ChangedBlock, ContentBlock, HTMLBlockType
from app.core.errors import NotFoundError
from app.db.schema import DBCriticalPage, DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.models.critical_page_models import (
    CriticalPageCreate,
    CriticalPageRead,
    CriticalPageUpdate,
)


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


def test_create_critical_page_with_recent_changes(session: Session, test_website: DBWebsite) -> None:
    """Tests creating a critical page populated with recent change attributes."""
    sample_block = ContentBlock(
        parent_heading="Introduction",
        block_type=HTMLBlockType.PARAGRAPH,
        text="Sample text content",
    )

    critical_page_details = CriticalPageCreate(
        url="https://www.test_website.com/test_recent_changes",
        website_id=test_website.id,
    )

    created_page: CriticalPageRead = CriticalPageService(session).create(critical_page_details)

    update_data: CriticalPageUpdate = CriticalPageUpdate(
        recent_links_added=["https://www.test_website.com/new-link"],
        recent_text_added=[sample_block],
    )

    updated_page: CriticalPageRead = CriticalPageService(session).update(id=created_page.id, model_update=update_data)

    assert updated_page.recent_links_added == ["https://www.test_website.com/new-link"]
    assert updated_page.recent_text_added
    assert len(updated_page.recent_text_added) == 1
    assert updated_page.recent_text_added[0].text == "Sample text content"
    assert updated_page.recent_text_added[0].block_type == HTMLBlockType.PARAGRAPH


def test_update_critical_page_complex_diff_fields(session: Session, test_critical_page: DBCriticalPage) -> None:
    """Tests updating and retrieving complex nested objects like ChangedBlock."""
    old_block = ContentBlock(parent_heading="Header", block_type=HTMLBlockType.PARAGRAPH, text="Old version")
    new_block = ContentBlock(parent_heading="Header", block_type=HTMLBlockType.PARAGRAPH, text="New version")
    changed_block_item = ChangedBlock(old_block=old_block, new_block=new_block, similarity=0.85)

    model_update: CriticalPageUpdate = CriticalPageUpdate(
        recent_text_changed=[changed_block_item],
        recent_documents_removed=["https://www.test_website.com/doc-v1.pdf"],
    )

    updated_critical_page: CriticalPageRead = CriticalPageService(session).update(
        id=test_critical_page.id, model_update=model_update
    )
    assert updated_critical_page.recent_text_changed
    assert len(updated_critical_page.recent_text_changed) == 1
    assert updated_critical_page.recent_text_changed[0].similarity == 0.85
    assert updated_critical_page.recent_text_changed[0].old_block.text == "Old version"
    assert updated_critical_page.recent_text_changed[0].new_block.text == "New version"
    assert updated_critical_page.recent_documents_removed == ["https://www.test_website.com/doc-v1.pdf"]

    fetched_page: CriticalPageRead = CriticalPageService(session).get(id=test_critical_page.id)
    assert fetched_page.recent_text_changed
    assert fetched_page.recent_text_changed[0].similarity == 0.85
    assert fetched_page.recent_documents_removed
    assert fetched_page.recent_documents_removed[0] == "https://www.test_website.com/doc-v1.pdf"
