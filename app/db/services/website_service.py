import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.backend.links import remove_repeated_pages
from app.core.config import config
from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBWebsite
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.crud_protocol import CRUDService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.recipient_service import RecipientService
from app.models.critical_page_models import CriticalPageCreate
from app.models.recipient_models import RecipientCreate
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)

MAX_DELAY: float = config.web_crawler_max_delay


class WebsiteService(CRUDService[WebsiteRead, WebsiteCreate, WebsiteUpdate]):
    def __init__(self, session: Session):
        """Initialises the WebsiteService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db: Session = session

    def get_all(self, skip: int = 0, limit: int | None = 100) -> Sequence[WebsiteRead]:
        """Retrieves a paginated list of the users website records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return, or None for every record. Defaults to 100.

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
            The matching WebsiteRead data model instance populated with critical pages and a count of internal links.

        Raises:
            NotFoundError: If no website record matches the provided UUID.
        """
        website_record: DBWebsite = repository.get(
            self._db,
            table=DBWebsite,
            id=id,
            relations=[DBWebsite.critical_pages],
        )
        return WebsiteRead.model_validate(website_record)

    def get_by_url(self, url: str) -> WebsiteRead:
        """Retrieves a single website record and its relationships matching a URL.

        Args:
            url: The target URL string of the website.

        Returns:
            The matching WebsiteRead data model instance populated with relationships.

        Raises:
            NotFoundError: If no matching website record exists for the provided URL.
        """
        website_records: Sequence[DBWebsite] = repository.get_list(
            self._db,
            table=DBWebsite,
            attributes={"url": url},
            relations=[DBWebsite.critical_pages],
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
        website_record: DBWebsite = DBWebsite(**model_create.model_dump(exclude={"critical_pages", "recipient_emails"}))
        repository.add(self._db, record=website_record)

        critical_page_service = CriticalPageService(self._db)
        # Each page is created once, even if written twice (e.g. "/news" and "/news/")
        for critical_page_url in remove_repeated_pages([model_create.url, *model_create.critical_pages]):
            critical_page_service.create(CriticalPageCreate(website_id=website_record.id, url=critical_page_url))
        self._db.expire(website_record, ["critical_pages"])

        recipient_service = RecipientService(self._db)
        for recipient_email in model_create.recipient_emails:
            try:
                recipient = recipient_service.get_by_email(recipient_email)
            except NotFoundError:
                recipient = recipient_service.create(RecipientCreate(email=recipient_email))

            recipient_service.link_recipient_and_website(
                website_id=website_record.id,
                recipient_id=recipient.id,
            )

        return WebsiteRead.model_validate(website_record)

    def update(self, id: uuid.UUID, model_update: WebsiteUpdate) -> WebsiteRead:
        """Updates attributes of an existing website record and syncs its sub-resources.

        Handles updates to website URLs, batch updates for monitored critical pages,
        and batch additions/removals of internal links. Re-activating a website clears
        why it was deactivated.

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
            exclude={"critical_page_updates", "recipient_emails"},
        )
        if model_update.active:
            update_data["deactivated_reason"] = None

        recipient_service = RecipientService(self._db)
        if model_update.add_recipient_emails:
            for recipient_email in model_update.add_recipient_emails:
                try:
                    recipient = recipient_service.get_by_email(recipient_email)
                except NotFoundError:
                    recipient = recipient_service.create(RecipientCreate(email=recipient_email))
                recipient_service.link_recipient_and_website(website_id=website_record.id, recipient_id=recipient.id)

        if model_update.remove_recipient_emails:
            for recipient_email in model_update.remove_recipient_emails:
                recipient = recipient_service.get_by_email(recipient_email)
                recipient_service.unlink_recipient_and_website(website_id=website_record.id, recipient_id=recipient.id)

        if update_data:
            website_record = repository.update(self._db, record=website_record, updates=update_data)

        if model_update.critical_page_updates:
            critical_page_service = CriticalPageService(self._db)
            for critical_page_id, critical_page_updates in model_update.critical_page_updates.items():
                critical_page_service.update(id=critical_page_id, model_update=critical_page_updates)

        internal_link_service = InternalLinkService(self._db)
        if model_update.initial_internal_links:
            internal_link_service.create_batch(
                urls=model_update.initial_internal_links,
                website_id=website_record.id,
            )
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
        # Deleted in bulk first, as deleting the website itself would load every link to delete them one by one
        InternalLinkService(self._db).delete_all_for_website(id)
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
        """Encapsulates rate-limit policy, throttling, cool downs, and deactivation logic.

        If the crawler is operating above minimum speed limits, throttles requests and sets
        a 24-hour cooldown. If already operating at minimum crawl speed, increments the
        consecutive failure counter and sets a 24-hour cooldown, automatically deactivating
        the website if the maximum failure threshold is reached.

        Args:
            website (WebsiteRead): The website record that encountered a traffic rate-limiting error.

        Returns:
            str: Status action message summarising the mitigation applied (throttled, cooldown, or deactivated).
        """

        is_at_min_speed: bool = (
            website.recommended_concurrent <= config.web_crawler_min_concurrent
            and website.recommended_delay >= config.web_crawler_max_delay
        )

        if not is_at_min_speed:
            self.throttle_and_cooldown(id=website.id, hours=config.website_cooldown_hours_after_throttle)
            return "Website throttled and placed on cooldown."

        # At minimum speed, manage consecutive failures
        self.set_cooldown(id=website.id, hours=config.website_cooldown_hours_after_throttle)

        if website.failed_attempts_at_min_speed >= config.web_crawler_max_failed_attempts_at_min_speed:
            self.update(id=website.id, model_update=WebsiteUpdate(active=False))
            return f"Failed {website.failed_attempts_at_min_speed} times. Deactivating: {website.url}"

        self.update(
            id=website.id,
            model_update=WebsiteUpdate(failed_attempts_at_min_speed=website.failed_attempts_at_min_speed + 1),
        )
        return "Website placed on cooldown."

    def handle_too_large(self, id: uuid.UUID, max_pages: int) -> str:
        """Deactivates a website with more pages than the crawler will scan, recording why so the
        dashboard can tell the user.

        Args:
            id (uuid.UUID): Unique identifier of the website that is too large to scan.
            max_pages (int): The most pages the crawler would scan.

        Returns:
            str: Status action message explaining the website has been deactivated.
        """
        website: WebsiteRead = self.update(
            id=id,
            model_update=WebsiteUpdate(active=False, deactivated_reason=DeactivationReason.TOO_LARGE),
        )
        logger.warning(f"Website {website.url} deactivated as it has more than {max_pages:,} pages.")
        return (
            f"This website has more than {max_pages:,} pages, which is more than the crawler will scan, "
            "so it has been deactivated. Its critical pages are still checked for changes, "
            "but the rest of the website is no longer scanned."
        )

    def handle_connection_error(self, website_id: uuid.UUID) -> str:
        """Handles unreachable site errors by setting a standard 2-hour cooldown period.

        Args:
            website_id (uuid.UUID): Unique identifier of the unreachable website.

        Returns:
            str: Status action message confirming cooldown placement.
        """
        self.set_cooldown(id=website_id, hours=config.website_cooldown_hours_after_unreachable)
        return "Website placed on cooldown."

    def reset_failed_attempts(self, id: uuid.UUID) -> None:
        """Resets the consecutive failed attempt counter to 0 upon a successful scan.

        If the website's `failed_attempts_at_min_speed` is greater than 0, updates the
        record in the database and logs the reset event.

        Args:
            id (uuid.UUID): Unique identifier of the website whose failure count should be reset.
        """
        website: WebsiteRead = self.get(id)
        if website.failed_attempts_at_min_speed > 0:
            self.update(id, model_update=WebsiteUpdate(failed_attempts_at_min_speed=0))
            logger.info(f"{website.url} has had its failed attempts reset")
