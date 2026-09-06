import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import repository
from app.db.models.critical_page_models import CriticalPageCreate
from app.db.models.internal_link_models import InternalLinkCreateBatch
from app.db.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.utils.interfaces import CRUDService
from app.db.errors import NotFoundError

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

    def get_by_url(self, url: str) -> WebsiteRead:
        """
        Retrieve a website and its relationships by URL.

        Raises:
            NotFoundError: If no website exists with the URL.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            attributes={"url": url},
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
        website_record: DBWebsite = DBWebsite(**model_create.model_dump(exclude={"critical_pages", "internal_links"}))
        repository.add(self._db, record=website_record)

        if model_create.critical_pages:
            [
                CriticalPageService(self._db).create(
                    CriticalPageCreate(website_id=website_record.id, **critical_page.model_dump())
                )
                for critical_page in model_create.critical_pages
            ]
        if model_create.internal_links:
            InternalLinkService(self._db).create_batch(
                model_create_batch=InternalLinkCreateBatch(
                    urls=model_create.internal_links,
                    website_id=website_record.id,
                )
            )

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

        website_record = repository.update(
            self._db,
            record=repository.get(self._db, table=DBWebsite, id=id),
            updates=model_update.model_dump(exclude_unset=True),
        )
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
