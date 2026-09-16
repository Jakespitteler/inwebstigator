import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.utils.interfaces import CRUDService
from app.models.critical_page_models import CriticalPageCreate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)


class WebsiteService(CRUDService[WebsiteRead, WebsiteCreate, WebsiteUpdate]):
    def __init__(self, session: Session):
        """Initialises the WebsiteService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[WebsiteRead]:
        """Retrieves a paginated list of website records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of WebsiteRead models representing the retrieved records.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(self._db, table=DBWebsite, skip=skip, limit=limit)
        return [WebsiteRead.model_validate(website_record) for website_record in website_records]

    def get(self, id: uuid.UUID) -> WebsiteRead:
        """Retrieves a single website record and its relationships by its unique primary key identifier.

        Args:
            id: The UUID identifier of the target website record.

        Returns:
            The matching WebsiteRead data model instance populated with internal links and critical pages.

        Raises:
            NotFoundError: If no website record matches the provided UUID.
        """
        website_record: DBWebsite = repository.get(
            self._db,
            table=DBWebsite,
            id=id,
            relations=[DBWebsite.internal_links, DBWebsite.critical_pages],
        )
        return WebsiteRead.model_validate(website_record)

    def get_by_url(self, url: str, user_id: uuid.UUID) -> WebsiteRead:
        """Retrieves a single website record and its relationships matching a URL and user ID.

        Args:
            url: The target URL string of the website.
            user_id: The UUID identifier of the owner user entity.

        Returns:
            The matching WebsiteRead data model instance populated with relationships.

        Raises:
            NotFoundError: If no matching website record exists for the provided URL and user ID.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            attributes={"url": url, "user_id": user_id},
            relations=[
                DBWebsite.internal_links,
                DBWebsite.critical_pages,
            ],
            limit=1,
        )

        if not website_records:
            raise NotFoundError(attributes={"url": url})

        return WebsiteRead.model_validate(website_records[0])

    def create(self, model_create: WebsiteCreate) -> WebsiteRead:
        """Creates and persists a new website record along with any associated critical pages.

        Args:
            model_create: The WebsiteCreate payload containing website attributes and critical page URLs.

        Returns:
            The created WebsiteRead data model instance reflecting saved state.

        Raises:
            IntegrityError: If the record violates database constraints or already exists.
        """
        website_record: DBWebsite = DBWebsite(**model_create.model_dump(exclude={"critical_pages"}))
        repository.add(self._db, record=website_record)

        if model_create.critical_pages:
            [
                CriticalPageService(self._db).create(
                    CriticalPageCreate(website_id=website_record.id, url=critical_page_url)
                )
                for critical_page_url in model_create.critical_pages
            ]

        return WebsiteRead.model_validate(website_record)

    def update(self, id: uuid.UUID, model_update: WebsiteUpdate) -> WebsiteRead:
        """Updates attributes of an existing website record and syncs its sub-resources.

        Handles updates to website URLs, batch updates for monitored critical pages,
        and batch additions/removals of internal links.

        Args:
            id: The UUID identifier of the website record to update.
            model_update: The WebsiteUpdate schema containing modified fields and sub-resource updates.

        Returns:
            The refreshed WebsiteRead data model instance following updates.

        Raises:
            NotFoundError: If the website record or any referenced sub-resource does not exist.
            IntegrityError: If updated attributes violate database constraints.
        """
        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)
        if model_update.url:
            website_record = repository.update(self._db, record=website_record, updates={"url": model_update.url})

        critical_page_service = CriticalPageService(self._db)
        if model_update.critical_page_updates:
            for critical_page_id, critical_page_updates in model_update.critical_page_updates.items():
                critical_page_service.update(id=critical_page_id, model_update=critical_page_updates)

        internal_link_service = InternalLinkService(self._db)
        if model_update.recent_added_internal_links:
            internal_link_service.create_batch(
                urls=model_update.recent_added_internal_links,
                website_id=website_record.id,
            )
        if model_update.recent_removed_internal_links:
            internal_link_service.delete_batch(
                urls=model_update.recent_removed_internal_links,
                website_id=website_record.id,
            )
        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)

        return WebsiteRead.model_validate(website_record)

    def delete(self, id: uuid.UUID) -> None:
        """Deletes a website record and associated resources from the database by its primary key.

        Args:
            id: The UUID identifier of the website record to remove.

        Raises:
            NotFoundError: If no website record matches the provided UUID.
        """
        repository.get(self._db, table=DBWebsite, id=id)  # Check if the record exists
        repository.delete(self._db, table=DBWebsite, id=id)
