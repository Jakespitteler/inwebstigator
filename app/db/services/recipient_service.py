import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from pydantic import EmailStr
from sqlalchemy import Exists, Select, select

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBRecipient, website_recipient_association
from app.db.services.base_crud_service import BaseCRUDService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate

logger: logging.Logger = logging.getLogger(__name__)


class RecipientService(BaseCRUDService[DBRecipient, RecipientRead, RecipientCreate, RecipientUpdate]):
    """Reads and writes the people emailed about websites, and which websites each one is emailed about."""

    table = DBRecipient
    read_model = RecipientRead

    def get_all_with_websites(self) -> Sequence[RecipientRead]:
        """Retrieves every recipient that is still linked to at least one website.

        A recipient removed from every website, or whose websites have all been deleted, is left out,
        as nothing is being monitored for them any more.

        Returns:
            A sequence of RecipientRead models for the recipients with at least one website.
        """
        is_linked_to_a_website: Exists = (
            select(website_recipient_association.c.recipient_id)
            .where(website_recipient_association.c.recipient_id == DBRecipient.id)
            .exists()
        )
        statement: Select[DBRecipient] = select(DBRecipient).where(is_linked_to_a_website)
        recipient_records: Sequence[DBRecipient] = self._db.scalars(statement).all()
        return [RecipientRead.model_validate(recipient_record) for recipient_record in recipient_records]

    def get_email_addresses(self) -> list[str]:
        """List all saved addresses for suggestions, including recipients not linked to a website."""
        addresses: Sequence[str] = self._db.scalars(select(DBRecipient.email)).all()
        return sorted(addresses, key=str.casefold)

    def get_by_email(self, email: EmailStr) -> RecipientRead:
        """Retrieves a single recipient record and its relationships by its associated email attribute.

        Args:
            email: The email string of the recipient record to retrieve.

        Returns:
            The matching RecipientRead data model instance.

        Raises:
            NotFoundError: If no recipient record exists with the specified email.
        """
        recipient_records: Sequence[DBRecipient] = repository.get_list(
            self._db,
            table=DBRecipient,
            attributes={"email": email},
            limit=1,
        )

        if not recipient_records:
            raise NotFoundError(attributes={"email": email})

        return RecipientRead.model_validate(recipient_records[0])

    def record_emailed(self, emails: Iterable[str], emailed_at: datetime) -> None:
        """Records that some recipients were just emailed, so their health checks count from then.

        Args:
            emails: The email addresses that were emailed, each already a recipient.
            emailed_at: When they were emailed.

        Raises:
            NotFoundError: If an email address is not a recipient.
        """
        for email in dict.fromkeys(emails):
            recipient: RecipientRead = self.get_by_email(email)
            self.update(id=recipient.id, model_update=RecipientUpdate(last_email_at=emailed_at))

    def _is_linked(self, website_id: uuid.UUID, recipient_id: uuid.UUID) -> bool:
        """Checks whether a recipient is already linked to a website.

        Args:
            website_id: The website.
            recipient_id: The recipient.

        Returns:
            True if the recipient is already emailed about the website.
        """
        link_exists: Exists = (
            select(website_recipient_association.c.recipient_id)
            .where(
                website_recipient_association.c.website_id == website_id,
                website_recipient_association.c.recipient_id == recipient_id,
            )
            .exists()
        )
        return bool(self._db.scalar(select(link_exists)))

    def link_recipient_and_website(self, website_id: uuid.UUID, recipient_id: uuid.UUID) -> None:
        """Links a recipient to a website, so they are emailed about it. Linking them again does nothing.

        The change is saved when the session's unit of work commits.

        Args:
            website_id: The website.
            recipient_id: The recipient.
        """
        if self._is_linked(website_id, recipient_id):
            return
        statement = website_recipient_association.insert().values(website_id=website_id, recipient_id=recipient_id)
        self._db.execute(statement)

    def unlink_recipient_and_website(self, website_id: uuid.UUID, recipient_id: uuid.UUID) -> None:
        """Unlinks a recipient from a website, so they are no longer emailed about it.

        The change is saved when the session's unit of work commits.

        Args:
            website_id: The website.
            recipient_id: The recipient.
        """
        statement = website_recipient_association.delete().where(
            website_recipient_association.c.website_id == website_id,
            website_recipient_association.c.recipient_id == recipient_id,
        )
        self._db.execute(statement)
