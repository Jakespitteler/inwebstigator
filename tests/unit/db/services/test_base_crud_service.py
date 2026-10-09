from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate

EMAILED_AT: datetime = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)


def test_get_all_returns_a_page_of_100_by_default_and_every_record_without_a_limit(session: Session) -> None:
    """Tests a service returns the first 100 records by default, the rest when they are skipped, and every record
    when the limit is None."""
    service = RecipientService(session)
    for number in range(101):
        service.create(RecipientCreate(email=f"recipient{number}@example.com"))

    assert len(service.get_all()) == 100
    assert len(service.get_all(skip=100)) == 1
    assert len(service.get_all(limit=None)) == 101


def test_update_only_saves_the_fields_that_were_set(session: Session, test_recipient: RecipientRead) -> None:
    """Tests updating one field leaves the record's other fields as they were."""
    service = RecipientService(session)
    service.update(test_recipient.id, RecipientUpdate(last_email_at=EMAILED_AT))

    updated: RecipientRead = service.update(test_recipient.id, RecipientUpdate(days_between_health_checks=3))

    assert (updated.email, updated.last_email_at, updated.days_between_health_checks) == (
        test_recipient.email,
        EMAILED_AT,
        3,
    )


def test_update_clears_a_field_set_to_none(session: Session, test_recipient: RecipientRead) -> None:
    """Tests a field set to None on the update is cleared, as it was set, unlike a field that was left out."""
    service = RecipientService(session)
    service.update(test_recipient.id, RecipientUpdate(last_email_at=EMAILED_AT))

    updated: RecipientRead = service.update(test_recipient.id, RecipientUpdate(last_email_at=None))

    assert updated.last_email_at is None
    assert service.get(test_recipient.id).last_email_at is None
