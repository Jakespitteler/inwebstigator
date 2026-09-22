import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import config
from app.core.errors import NotFoundError, NotLoggedInError
from app.db import repository
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.utils.interfaces import CRUDService
from app.models.critical_page_models import CriticalPageCreate
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)

MAX_DELAY: float = config.web_crawler_max_delay


class WebsiteService(CRUDService[WebsiteRead, WebsiteCreate, WebsiteUpdate]):
    def __init__(self, session: Session):
        """Initialises the WebsiteService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db: Session = session

        if not config.user_id:
            raise NotLoggedInError()
        self.user_id: uuid.UUID = config.user_id

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[WebsiteRead]:
        """Retrieves a paginated list of the users website records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of WebsiteRead models representing the retrieved records.
        """

        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            skip=skip,
            limit=limit,
            attributes={"user_id": self.user_id},
        )
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

    def get_by_url(self, url: str) -> WebsiteRead:
        """Retrieves a single website record and its relationships matching a URL and user ID.

        Args:
            url: The target URL string of the website.

        Returns:
            The matching WebsiteRead data model instance populated with relationships.

        Raises:
            NotFoundError: If no matching website record exists for the provided URL and user ID.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            attributes={"url": url, "user_id": self.user_id},
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
        website_record: DBWebsite = DBWebsite(
            user_id=self.user_id, **model_create.model_dump(exclude={"critical_pages"})
        )
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

        update_data = model_update.model_dump(
            exclude_unset=True,
            exclude={
                "critical_page_updates",
                "recent_added_internal_links",
                "recent_removed_internal_links",
            },
        )

        if update_data:
            website_record = repository.update(self._db, record=website_record, updates=update_data)

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

    def set_cooldown(self, id: uuid.UUID, hours: int) -> WebsiteRead:
        """Sets a cooldown expiration timestamp on a website record.

        Args:
            id: The UUID identifier of the target website record.
            hours: The number of hours from now to keep the website on cooldown.

        Returns:
            The refreshed WebsiteRead data model instance reflecting the updated cooldown state.

        Raises:
            NotFoundError: If no website record matches the provided UUID.
        """
        on_cooldown_until = datetime.now() + timedelta(hours=hours)

        website_record = repository.update(
            self._db,
            record=repository.get(self._db, table=DBWebsite, id=id),
            updates=WebsiteUpdate(on_cooldown_until=on_cooldown_until).model_dump(exclude_unset=True),
        )
        logger.warning(f"Website {website_record.url} placed on cooldown until {on_cooldown_until}.")
        return WebsiteRead.model_validate(website_record)

    def throttle_and_cooldown(self, id: uuid.UUID, hours: int = 24) -> WebsiteRead:
        """Increases crawler delay, decreases concurrency limits, and sets a cooldown period.

        Args:
            id: The UUID identifier of the target website record.
            hours: The number of hours to keep the website on cooldown. Defaults to 24.

        Returns:
            The refreshed WebsiteRead data model instance reflecting updated throttling and cooldown settings.

        Raises:
            NotFoundError: If no website record matches the provided UUID.
        """
        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)

        new_delay: float = min(config.web_crawler_max_delay, website_record.recommended_delay + 0.5)
        new_concurrent: int = max(config.web_crawler_min_concurrent, website_record.recommended_concurrent // 2)

        on_cooldown_until = datetime.now() + timedelta(hours=hours)

        updated_record = repository.update(
            self._db,
            record=website_record,
            updates=WebsiteUpdate(
                recommended_delay=new_delay,
                recommended_concurrent=new_concurrent,
                on_cooldown_until=on_cooldown_until,
            ).model_dump(exclude_unset=True),
        )

        logger.warning(
            f"Website {website_record.url} throttled ({new_delay=}s, {new_concurrent=}) "
            f"and placed on cooldown until {on_cooldown_until}."
        )
        return WebsiteRead.model_validate(updated_record)

    def handle_traffic_error(self, website: WebsiteRead) -> str:
        """Encapsulates rate-limit policy, throttling, cool downs, and deactivation logic."""

        is_at_min_speed: bool = (
            website.recommended_concurrent <= config.web_crawler_min_concurrent
            and website.recommended_delay >= config.web_crawler_max_delay
        )

        if not is_at_min_speed:
            self.throttle_and_cooldown(id=website.id, hours=24)
            return "Website throttled and placed on cooldown."

        # At minimum speed, manage consecutive failures
        self.set_cooldown(id=website.id, hours=24)

        if website.failed_attempts_at_min_speed >= config.web_crawler_max_failed_attempts_at_min_speed:
            self.update(id=website.id, model_update=WebsiteUpdate(active=False))
            return f"Failed {website.failed_attempts_at_min_speed} times. Deactivating: {website.url}"

        self.update(
            id=website.id,
            model_update=WebsiteUpdate(failed_attempts_at_min_speed=website.failed_attempts_at_min_speed + 1),
        )
        return "Website placed on cooldown."

    def handle_connection_error(self, website_id: uuid.UUID) -> str:
        """Handles unreachable site error by setting a standard cooldown."""
        self.set_cooldown(id=website_id, hours=2)
        return "Website placed on cooldown."

    def reset_failed_attempts(self, id: uuid.UUID) -> None:
        """On scan success the failed attempts are reset if they are not already 0."""
        website: WebsiteRead = self.get(id)
        if website.failed_attempts_at_min_speed > 0:
            self.update(id, model_update=WebsiteUpdate(failed_attempts_at_min_speed=0))
            logger.info(f"{website.url} has had its failed attempts reset")
