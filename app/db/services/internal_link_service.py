import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBInternalLink
from app.db.utils.field_types import URLString
from app.db.utils.interfaces import CRUDService
from app.models.internal_link_models import (
    InternalLinkCreate,
    InternalLinkRead,
    InternalLinkUpdate,
)

logger: logging.Logger = logging.getLogger(__name__)


class InternalLinkService(CRUDService[InternalLinkRead, InternalLinkCreate, InternalLinkUpdate]):
    def __init__(self, session: Session):
        """Initialises the InternalLinkService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[InternalLinkRead]:
        """Retrieves a paginated list of internal link records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of InternalLinkRead models representing the retrieved records.
        """
        internal_link_records: Sequence[DBInternalLink] = repository.get_list(
            self._db, table=DBInternalLink, skip=skip, limit=limit
        )
        return [InternalLinkRead.model_validate(internal_link_record) for internal_link_record in internal_link_records]

    def get(self, id: uuid.UUID) -> InternalLinkRead:
        """Retrieves a single internal link record by its unique primary key identifier.

        Args:
            id: The UUID identifier of the target internal link record.

        Returns:
            The matching InternalLinkRead data model instance.

        Raises:
            NotFoundError: If no internal link record matches the provided UUID.
        """
        internal_link_record: DBInternalLink = repository.get(self._db, table=DBInternalLink, id=id)
        return InternalLinkRead.model_validate(internal_link_record)

    def get_by_url(self, url: str) -> InternalLinkRead:
        """Retrieves a single internal link record by its URL attribute.

        Args:
            url: The URL string of the internal link to retrieve.

        Returns:
            The matching InternalLinkRead data model instance.

        Raises:
            NotFoundError: If no internal link record exists with the specified URL.
        """
        internal_link_records: Sequence[DBInternalLink] = repository.get_list(
            self._db,
            table=DBInternalLink,
            attributes={"url": url},
            limit=1,
        )
        if not internal_link_records:
            raise NotFoundError(attributes={"url": url})

        return InternalLinkRead.model_validate(internal_link_records[0])

    def create(self, model_create: InternalLinkCreate) -> InternalLinkRead:
        """Creates and persists a single new internal link record in the database.

        Args:
            model_create: The InternalLinkCreate payload containing initial attributes.

        Returns:
            The created InternalLinkRead data model instance reflecting the saved state.

        Raises:
            IntegrityError: If the record violates database constraints or already exists.
        """
        internal_link_record: DBInternalLink = DBInternalLink(**model_create.model_dump())
        repository.add(self._db, record=internal_link_record)
        return InternalLinkRead.model_validate(internal_link_record)

    def create_batch(self, urls: Sequence[URLString], website_id: uuid.UUID) -> Sequence[InternalLinkRead]:
        """Creates multiple internal link records in a single database batch operation.

        Args:
            urls: A sequence of URL strings to create as internal links.
            website_id: The UUID identifier of the parent website entity.

        Returns:
            A sequence of created InternalLinkRead models representing the newly added records.

        Raises:
            IntegrityError: If any internal link violates database unique or foreign key constraints.
        """
        internal_link_records = [DBInternalLink(url=url, website_id=website_id) for url in urls]

        repository.batch_add(self._db, records=internal_link_records)

        return [InternalLinkRead.model_validate(record) for record in internal_link_records]

    def update(self, id: uuid.UUID, model_update: InternalLinkUpdate) -> InternalLinkRead:
        """Updates attributes of an existing internal link record by its primary key.

        Args:
            id: The UUID identifier of the internal link record to update.
            model_update: The InternalLinkUpdate schema containing fields to update.

        Returns:
            The updated InternalLinkRead data model instance.

        Raises:
            NotFoundError: If no internal link record matches the provided UUID.
            IntegrityError: If updated attribute values violate database constraints.
        """
        internal_link_record: DBInternalLink = repository.get(self._db, table=DBInternalLink, id=id)
        internal_link_record = repository.update(
            self._db, record=internal_link_record, updates=model_update.model_dump(exclude_unset=True)
        )
        return InternalLinkRead.model_validate(internal_link_record)

    def delete(self, id: uuid.UUID) -> None:
        """Deletes an internal link record from the database by its primary key.

        Args:
            id: The UUID identifier of the internal link record to remove.

        Raises:
            NotFoundError: If no internal link record matches the provided UUID.
        """
        repository.get(self._db, table=DBInternalLink, id=id)
        repository.delete(self._db, table=DBInternalLink, id=id)

    def delete_batch(self, urls: Sequence[str], website_id: uuid.UUID) -> None:
        """Deletes multiple internal link records matching a sequence of URLs and a website ID.

        Args:
            urls: Sequence of target URL strings to delete.
            website_id: The UUID identifier of the associated website entity.

        Raises:
            NotFoundError: If `urls` are provided but any specified target record does not exist.
            IntegrityError: If batch deletion violates database constraints.
        """

        repository.batch_delete(
            self._db,
            table=DBInternalLink,
            attributes={"url": list(urls), "website_id": website_id},
        )
