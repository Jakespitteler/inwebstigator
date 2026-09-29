import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBRecipient, website_recipient_association
from app.db.utils.field_types import EmailString
from app.db.utils.interfaces import CRUDService
from app.models.recipient_models import RecipientCreate, RecipientRead, RecipientUpdate

logger: logging.Logger = logging.getLogger(__name__)


class RecipientService(CRUDService[RecipientRead, RecipientCreate, RecipientUpdate]):
    def __init__(self, session: Session):
        """Initialises the RecipientService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[RecipientRead]:
        """Retrieves a paginated list of recipient records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of RecipientRead models representing the retrieved records.
        """
        recipient_records: Sequence[DBRecipient] = repository.get_list(
            self._db,
            table=DBRecipient,
            skip=skip,
            limit=limit,
        )
        return [RecipientRead.model_validate(recipient_record) for recipient_record in recipient_records]

    def get(self, id: uuid.UUID) -> RecipientRead:
        """Retrieves a single recipient record by its unique primary key identifier.

        Args:
            id: The UUID identifier of the target recipient record.

        Returns:
            The matching RecipientRead data model instance.

        Raises:
            NotFoundError: If no recipient record matches the provided UUID.
        """
        recipient_record: DBRecipient = repository.get(self._db, table=DBRecipient, id=id)
        return RecipientRead.model_validate(recipient_record)

    def get_by_email(self, email: EmailString) -> RecipientRead:
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

    def create(self, model_create: RecipientCreate) -> RecipientRead:
        """Creates and persists a new recipient record in the database.

        Args:
            model_create: The RecipientCreate payload containing initial attributes.

        Returns:
            The created RecipientRead data model instance reflecting the saved state.

        Raises:
            IntegrityError: If the record violates database constraints or already exists.
        """
        recipient_record: DBRecipient = DBRecipient(**model_create.model_dump())
        repository.add(self._db, record=recipient_record)

        return RecipientRead.model_validate(recipient_record)

    def update(self, id: uuid.UUID, model_update: RecipientUpdate) -> RecipientRead:
        """Updates attributes of an existing recipient record by its primary key.

        Args:
            id: The UUID identifier of the recipient record to update.
            model_update: The RecipientUpdate schema containing fields to update.

        Returns:
            The updated RecipientRead data model instance.

        Raises:
            NotFoundError: If no recipient record matches the provided UUID.
            IntegrityError: If updated attribute values violate database constraints.
        """

        recipient_record: DBRecipient = repository.update(
            self._db,
            record=repository.get(self._db, table=DBRecipient, id=id),
            updates=model_update.model_dump(exclude_unset=True),
        )
        return RecipientRead.model_validate(recipient_record)

    def delete(self, id: uuid.UUID) -> None:
        """Deletes a recipient record from the database by its primary key.

        Args:
            id: The UUID identifier of the recipient record to remove.

        Raises:
            NotFoundError: If no recipient record matches the provided UUID.
        """
        repository.get(self._db, table=DBRecipient, id=id)  # Check if the record exists
        repository.delete(self._db, table=DBRecipient, id=id)

    def link_recipient_and_website(self, website_id: uuid.UUID, recipient_id: uuid.UUID):
        statement = website_recipient_association.insert().values(
            website_id=website_id,
            recipient_id=recipient_id,
        )
        self._db.execute(statement)
        self._db.commit()

    def unlink_recipient_and_website(self, website_id: uuid.UUID, recipient_id: uuid.UUID):
        statement = website_recipient_association.delete().where(
            website_recipient_association.c.website_id == website_id,
            website_recipient_association.c.recipient_id == recipient_id,
        )
        self._db.execute(statement)
        self._db.commit()
