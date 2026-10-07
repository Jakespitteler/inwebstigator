import logging
import uuid
from collections.abc import Sequence

from pydantic import HttpUrl
from sqlalchemy import Delete, Select, delete, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBInternalLink
from app.db.services.crud_protocol import CRUDService
from app.models.internal_link_models import (
    InternalLinkCreate,
    InternalLinkRead,
    InternalLinkUpdate,
)

logger: logging.Logger = logging.getLogger(__name__)

URLS_PER_DELETE: int = 500


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

    def get_by_url(self, url: HttpUrl) -> InternalLinkRead:
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

    def get_urls_for_website(self, website_id: uuid.UUID) -> list[str]:
        """Retrieves the URL of every internal link saved for a website.

        Only the URLs are read, not whole records, so a large website's tens of thousands of links load quickly.

        Args:
            website_id: The UUID identifier of the website.

        Returns:
            The URLs of the website's internal links.
        """
        statement: Select[str] = select(DBInternalLink.url).where(DBInternalLink.website_id == website_id)
        return list(self._db.scalars(statement).all())

    def create_batch(self, urls: Sequence[HttpUrl], website_id: uuid.UUID) -> None:
        """Creates internal link records for a website in one bulk insert.

        The records are not read back after saving, so a large website's tens of thousands of links
        are saved in seconds.

        Args:
            urls: A sequence of URL strings to create as internal links.
            website_id: The UUID identifier of the parent website entity.

        Raises:
            IntegrityError: If any internal link violates database unique or foreign key constraints.
        """
        repository.bulk_insert(
            self._db, table=DBInternalLink, rows=[{"url": url, "website_id": website_id} for url in urls]
        )

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

    def delete_batch(self, urls: Sequence[HttpUrl], website_id: uuid.UUID) -> None:
        """Deletes a website's internal link records matching a sequence of URLs.

        The links are deleted in bulk, a chunk of URLs at a time, without loading each record first.
        URLs with no saved link are ignored.

        Args:
            urls: Sequence of target URL strings to delete.
            website_id: The UUID identifier of the associated website entity.
        """
        for start in range(0, len(urls), URLS_PER_DELETE):
            statement: Delete = delete(DBInternalLink).where(
                DBInternalLink.website_id == website_id,
                DBInternalLink.url.in_(urls[start : start + URLS_PER_DELETE]),
            )
            self._db.execute(statement)

    def delete_all_for_website(self, website_id: uuid.UUID) -> None:
        """Deletes every internal link record of a website in one bulk delete, without loading each record first.

        Args:
            website_id: The UUID identifier of the website.
        """
        self._db.execute(delete(DBInternalLink).where(DBInternalLink.website_id == website_id))
