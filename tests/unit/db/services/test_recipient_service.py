from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.core.errors import IntegrityError, NotFoundError
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate
from app.models.website_models import WebsiteCreate, WebsiteRead


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


def test_get_all_with_websites_leaves_out_recipients_without_a_website(
    session: Session, test_website: WebsiteRead
) -> None:
    """
    Tests only recipients linked to at least one website are returned, including after a website is deleted.

    Args:
        session: The database session fixture.
        test_website: The test website record, whose recipient is the test recipient.
    """
    recipient_service = RecipientService(session)
    without_website: RecipientRead = recipient_service.create(RecipientCreate(email="no-website@gmail.com"))

    fetched_emails = {recipient.email for recipient in recipient_service.get_all_with_websites()}
    assert fetched_emails == {recipient.email for recipient in test_website.recipients}
    assert without_website.email not in fetched_emails

    WebsiteService(session).delete(test_website.id)
    assert recipient_service.get_all_with_websites() == []


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


def test_delete_recipient(session: Session, test_recipient: RecipientRead, test_website: WebsiteRead) -> None:
    """
    Tests deleting an existing recipient and cascading its children.

    Args:
        session: The database session fixture.
        test_recipient: The test recipient record.
        test_website: The test website record, whose recipient is the test recipient.
    """
    RecipientService(session).delete(id=test_recipient.id)

    # Check the recipient can no longer be retrieved
    with pytest.raises(NotFoundError):
        RecipientService(session).get(id=test_recipient.id)

    # Check its link to the website is deleted with it, but the website is kept
    session.expire_all()
    assert WebsiteService(session).get(test_website.id).recipients == []


def test_get_by_email_raises_not_found_for_an_unknown_email(session: Session) -> None:
    """Tests looking up an email that is not a recipient raises NotFoundError."""
    with pytest.raises(NotFoundError):
        RecipientService(session).get_by_email("nobody@example.com")


def test_the_same_email_cannot_be_added_twice(session: Session, test_recipient: RecipientRead) -> None:
    """Tests an email that is already a recipient cannot be added as a second recipient."""
    with pytest.raises(IntegrityError):
        RecipientService(session).create(RecipientCreate(email=test_recipient.email))


def test_record_emailed_sets_when_each_recipient_was_emailed(session: Session, test_recipient: RecipientRead) -> None:
    """Tests each emailed recipient is given the time they were emailed, even if listed twice, and other recipients
    are left alone."""
    recipient_service = RecipientService(session)
    other_recipient: RecipientRead = recipient_service.create(RecipientCreate(email="other@example.com"))
    emailed_at = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)

    recipient_service.record_emailed([test_recipient.email, test_recipient.email], emailed_at=emailed_at)

    assert recipient_service.get(test_recipient.id).last_email_at == emailed_at
    assert recipient_service.get(other_recipient.id).last_email_at is None


def test_record_emailed_raises_not_found_for_an_unknown_email(session: Session) -> None:
    """Tests recording an email to an address that is not a recipient raises NotFoundError."""
    with pytest.raises(NotFoundError):
        RecipientService(session).record_emailed(["nobody@example.com"], emailed_at=datetime.now(UTC))


def test_linking_a_recipient_twice_links_them_once(
    session: Session, test_recipient: RecipientRead, test_website: WebsiteRead
) -> None:
    """Tests linking a recipient already on a website again does not fail or add a second link."""
    RecipientService(session).link_recipient_and_website(website_id=test_website.id, recipient_id=test_recipient.id)
    session.expire_all()

    assert [recipient.id for recipient in WebsiteService(session).get(test_website.id).recipients] == [
        test_recipient.id
    ]


def test_unlinking_a_recipient_only_removes_them_from_that_website(
    session: Session, test_recipient: RecipientRead, test_website: WebsiteRead
) -> None:
    """Tests a recipient unlinked from one website is no longer emailed about it, but stays a recipient of their other
    websites."""
    website_service = WebsiteService(session)
    other_website: WebsiteRead = website_service.create(
        WebsiteCreate(url=HttpUrl("https://other.example.com"), recipient_emails=[test_recipient.email])
    )

    RecipientService(session).unlink_recipient_and_website(website_id=test_website.id, recipient_id=test_recipient.id)
    session.expire_all()

    assert website_service.get(test_website.id).recipients == []
    assert [recipient.id for recipient in website_service.get(other_website.id).recipients] == [test_recipient.id]
    assert RecipientService(session).get(test_recipient.id).email == test_recipient.email
