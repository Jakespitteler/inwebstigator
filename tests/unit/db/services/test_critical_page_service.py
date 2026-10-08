import re
import uuid
from collections.abc import Sequence

import pytest
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.core.errors import IntegrityError, NotFoundError
from app.db.services.critical_page_service import CriticalPageService
from app.models.critical_page_models import (
    CriticalPageCreate,
    CriticalPageRead,
    CriticalPageUpdate,
)
from app.models.website_models import WebsiteRead


def test_get_all_critical_pages(session: Session, test_critical_page: CriticalPageRead) -> None:
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


def test_get_critical_page(session: Session, test_critical_page: CriticalPageRead) -> None:
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


def test_create_critical_page(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests creating a new critical page with basic details.

    Args:
        session: The database session fixture.
    """
    critical_page_details = CriticalPageCreate(
        url=HttpUrl("https://www.test_website.com/test_critical_page"),
        website_id=test_website.id,
    )

    created_critical_page: CriticalPageRead = CriticalPageService(session).create(critical_page_details)
    assert created_critical_page.id is not None
    assert created_critical_page.url == critical_page_details.url

    fetched_critical_page: CriticalPageRead = CriticalPageService(session).get(id=created_critical_page.id)
    assert fetched_critical_page.url == critical_page_details.url


def test_update_critical_page(session: Session, test_critical_page: CriticalPageRead) -> None:
    """
    Tests updating an existing critical_page's details.

    Args:
        session: The database session fixture.
        test_critical_page: The test critical page record.
    """
    model_update = CriticalPageUpdate(links=[HttpUrl("https://www.test_website.com/updated_critical_page_link")])

    updated_critical_page: CriticalPageRead = CriticalPageService(session).update(
        id=test_critical_page.id, model_update=model_update
    )
    assert updated_critical_page.id == test_critical_page.id
    assert updated_critical_page.links == model_update.links

    fetched_critical_page: CriticalPageRead = CriticalPageService(session).get(id=test_critical_page.id)
    assert fetched_critical_page.links == model_update.links


def test_delete_critical_page(session: Session, test_critical_page: CriticalPageRead) -> None:
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


def test_a_website_cannot_watch_the_same_critical_page_twice(
    session: Session, test_critical_page: CriticalPageRead
) -> None:
    """Tests a critical page already watched on a website cannot be added to it again."""
    with pytest.raises(IntegrityError):
        CriticalPageService(session).create(
            CriticalPageCreate(url=test_critical_page.url, website_id=test_critical_page.website_id)
        )


def test_a_critical_page_of_a_website_that_does_not_exist_is_refused(session: Session) -> None:
    """Tests a critical page cannot be saved for a website that does not exist, as foreign keys are checked."""
    with pytest.raises(IntegrityError):
        CriticalPageService(session).create(
            CriticalPageCreate(url=HttpUrl("https://www.test_website.com/fees"), website_id=uuid.uuid4())
        )


def test_ignore_rules_are_read_back_as_the_patterns_they_were_written_as(
    session: Session, test_website: WebsiteRead
) -> None:
    """Tests a critical page's ignore rules are saved as their pattern text, not as Python's "re.compile(...)"."""
    created_page: CriticalPageRead = CriticalPageService(session).create(
        CriticalPageCreate(
            url=HttpUrl("https://www.test_website.com/fees"),
            website_id=test_website.id,
            ignore_rules=[re.compile(r"Last updated \d+")],
        )
    )
    session.expire_all()

    assert CriticalPageService(session).get(created_page.id).ignore_rules == [r"Last updated \d+"]
