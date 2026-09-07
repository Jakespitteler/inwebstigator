import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import config
from app.db import repository
from app.db.errors import NotFoundError
from app.db.models.critical_page_models import CriticalPageCreate
from app.db.models.internal_link_models import InternalLinkCreateBatch
from app.db.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.utils.interfaces import CRUDService

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
        Retrieves a single website record by its url.

        Args:
            url: The url of the website to retrieve.

        Raises:
            NotFoundError: If no website exists with the provided URL.

        Returns:
            The retrieved website.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            attributes={"url": url},
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

    def throttle_and_cooldown(
        self, id: uuid.UUID, hours: int = 24
    ) -> WebsiteRead:  # TODO ===== Tests for these and doc strings ======
        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)

        new_delay: float = min(config.web_crawler_max_delay, website_record.recommended_delay + 0.5)
        new_concurrent: int = max(1, website_record.recommended_concurrent // 2)

        cooldown_until = datetime.now() + timedelta(hours=hours)

        updated_record = repository.update(
            self._db,
            record=website_record,
            updates=WebsiteUpdate(
                recommended_delay=new_delay,
                recommended_concurrent=new_concurrent,
                next_scan_at=cooldown_until,
            ).model_dump(exclude_unset=True),
        )

        logger.warning(
            f"Website {website_record.url} throttled ({new_delay=}s, {new_concurrent=}) "
            f"and placed on cooldown until {cooldown_until}."
        )
        return WebsiteRead.model_validate(updated_record)

    def set_cooldown(self, id: uuid.UUID, hours: int) -> WebsiteRead:
        cooldown_until = datetime.now() + timedelta(hours=hours)

        website_record = repository.update(
            self._db,
            record=repository.get(self._db, table=DBWebsite, id=id),
            updates=WebsiteUpdate(next_scan_at=cooldown_until).model_dump(exclude_unset=True),
        )
        logger.warning(f"Website {website_record.url} placed on cooldown until {cooldown_until}.")
        return WebsiteRead.model_validate(website_record)
