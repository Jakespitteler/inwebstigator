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
        """_summary_

        Args:
            session (Session): The database session.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[WebsiteRead]:
        """
        Retrieves website records.

        Args:
            skip: The number of records to skip.
            limit: The maximum number of records to return.

        Returns:
            The retrieved websites.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(self._db, table=DBWebsite, skip=skip, limit=limit)
        return [WebsiteRead.model_validate(website_record) for website_record in website_records]

    def get(self, id: uuid.UUID) -> WebsiteRead:
        """
        Retrieves a single website by its primary key.

        Args:
            id: The id of the website to retrieve.

        Raises:
            NotFoundError: If no website exists with the provided ID.

        Returns:
            The retrieved website.
        """
        website_record: DBWebsite = repository.get(
            self._db,
            table=DBWebsite,
            id=id,
            relations=[DBWebsite.internal_links, DBWebsite.critical_pages],
        )
        return WebsiteRead.model_validate(website_record)

    def get_by_url(self, url: str, user_id: uuid.UUID) -> WebsiteRead:
        """
        Retrieve a website and its relationships by URL and user ID.

        Raises:
            NotFoundError: If no website exists with the URL.
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
        """
        Creates a new website record.

        Args:
            model_create: The website details to create.

        Raises:
            IntegrityError: If the website already exists in db.

        Returns:
            The website record.
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
        """
        Updates an existing website record.

        Args:
            id: The id of the website to update.
            model_update: The new data to apply to the website.

        Raises:
            NotFoundError: If the website with id does not exist.
            IntegrityError: If the website updated details already exists in db.

        Returns:
            The updated website.
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
        """
        Deletes a website by its primary key.

        Args:
            id: The id of the website to delete.

        Raises:
            NotFoundError: If no website exists with the provided ID.
        """
        repository.get(self._db, table=DBWebsite, id=id)  # Check if the record exists
        repository.delete(self._db, table=DBWebsite, id=id)
