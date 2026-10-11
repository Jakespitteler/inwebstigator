"""Tests of the recipient models: what is accepted when a recipient is added or changed."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.recipient_models import RecipientCreate, RecipientUpdate


def test_a_recipient_that_is_not_an_email_address_is_refused() -> None:
    """Tests a recipient cannot be added with an address that is not an email address."""
    with pytest.raises(ValidationError, match="email address"):
        RecipientCreate(email="not-an-email")


def test_a_recipients_last_email_time_must_have_a_time_zone() -> None:
    """Tests a last email time without a time zone is refused, as it cannot be saved as UTC, while one with a time
    zone is kept."""
    emailed_at = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="timezone"):
        RecipientUpdate.model_validate({"last_email_at": "2026-10-08T10:00:00"})
    assert RecipientUpdate(last_email_at=emailed_at).last_email_at == emailed_at
