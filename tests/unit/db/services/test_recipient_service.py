from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate


def test_get_all_recipients(session: Session, test_recipient: RecipientRead) -> None:
    """
    Tests retrieving a list of all recipients.

    Args:
        session: The database session fixture.
        test_recipient: The test recipient record.
    """
    fetched_recipients: Sequence[RecipientRead] = RecipientService(session).get_all()
    assert len(fetched_recipients) >= 1
    assert any(c.id == test_recipient.id for c in fetched_recipients)
    assert any(c.email == test_recipient.email for c in fetched_recipients)


def test_get_recipient(session: Session, test_recipient: RecipientRead) -> None:
    """
    Tests retrieving an existing recipient by ID.

    Args:
        session: The database session fixture.
        test_recipient: The test recipient record.
    """
    fetched_recipient: RecipientRead = RecipientService(session).get(id=test_recipient.id)

    assert fetched_recipient is not None
    assert fetched_recipient.id == test_recipient.id
    assert fetched_recipient.email == test_recipient.email


def test_create_recipient(session: Session) -> None:
    """
    Tests creating a new recipient with basic details.

    Args:
        session: The database session fixture.
    """
    recipient_details = RecipientCreate(email="test_recipient@email.com")

    created_recipient: RecipientRead = RecipientService(session).create(recipient_details)
    assert created_recipient.id is not None
    assert created_recipient.email == recipient_details.email

    fetched_recipient: RecipientRead = RecipientService(session).get(id=created_recipient.id)
    assert fetched_recipient.email == recipient_details.email


def test_update_recipient(session: Session, test_recipient: RecipientRead) -> None:
    """
    Tests updating an existing recipient's details.

    Args:
        session: The database session fixture.
        test_recipient: The test recipient record.
    """
    model_update = RecipientUpdate(email="updated_recipient@email.com")

    updated_recipient: RecipientRead = RecipientService(session).update(id=test_recipient.id, model_update=model_update)
    assert updated_recipient.id == test_recipient.id
    assert updated_recipient.email == model_update.email

    fetched_recipient: RecipientRead = RecipientService(session).get(id=test_recipient.id)
    assert fetched_recipient.email == model_update.email


def test_delete_recipient(session: Session, test_recipient: RecipientRead) -> None:
    """
    Tests deleting an existing recipient and cascading its children.

    Args:
        session: The database session fixture.
        test_recipient: The test recipient record.
    """
    RecipientService(session).delete(id=test_recipient.id)

    # Check the recipient can no longer be retrieved
    with pytest.raises(NotFoundError):
        RecipientService(session).get(id=test_recipient.id)
