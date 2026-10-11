import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import HttpUrl
from sqlalchemy import select

from app.core.errors import NotFoundError, WebsiteAlreadyMonitoredError
from app.core.urls import is_same_page, page_key, remove_repeated_pages
from app.db import repository
from app.db.schema import DBWebsite
from app.db.services.base_crud_service import BaseCRUDService
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.recipient_service import RecipientService
from app.models.critical_page_models import CriticalPageCreate
from app.models.recipient_models import RecipientCreate
from app.models.scan_result_models import WebsiteScanResult
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteUpdate

logger: logging.Logger = logging.getLogger(__name__)


class WebsiteService(BaseCRUDService[DBWebsite, WebsiteRead, WebsiteCreate, WebsiteUpdate]):
    """Reads and writes the monitored websites, with their critical pages, recipients and internal links."""

    table = DBWebsite
    read_model = WebsiteRead

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

    def get_by_url(self, url: HttpUrl) -> WebsiteRead:
        """Retrieves a single website record and its relationships matching a URL.

        The URL matches however it is written, e.g. with or without "www.", a trailing "/",
        or http instead of https, so the same website is never treated as two.

        Args:
            url: The target URL string of the website.

        Returns:
            The matching WebsiteRead data model instance populated with relationships.

        Raises:
            NotFoundError: If no matching website record exists for the provided URL.
        """
        url_key: str = page_key(str(url))
        # Only the ids and URLs are loaded to search, as a website's internal links can number thousands
        for website_id, website_url in self._db.execute(select(DBWebsite.id, DBWebsite.url)).all():
            if page_key(website_url) == url_key:
                return self.get(website_id)

        raise NotFoundError(attributes={"url": url})

    def create(self, model_create: WebsiteCreate) -> WebsiteRead:
        """Creates and persists a new website record along with any associated critical pages.

        Args:
            model_create: The WebsiteCreate payload containing website attributes and critical page URLs.

        Returns:
            The created WebsiteRead data model instance reflecting saved state.

        Raises:
            WebsiteAlreadyMonitoredError: If the website is already being monitored, even if written differently.
            IntegrityError: If the record violates database constraints or already exists.
        """
        try:
            existing_website: WebsiteRead = self.get_by_url(model_create.url)
        except NotFoundError:
            pass
        else:
            raise WebsiteAlreadyMonitoredError(str(existing_website.url))

        website_record: DBWebsite = DBWebsite(**model_create.model_dump(exclude={"critical_pages", "recipient_emails"}))
        repository.add(self._db, record=website_record)

        critical_page_service = CriticalPageService(self._db)
        # Each page is created once, even if written twice (e.g. "/news" and "/news/"). The home page is always the
        # website's own address, so the dashboard and the scans can tell which page it is.
        website_url: str = str(model_create.url)
        other_pages: list[str] = [
            page_url
            for page_url in remove_repeated_pages(model_create.critical_pages)
            if not is_same_page(page_url, website_url)
        ]
        for critical_page_url in [website_url, *other_pages]:
            critical_page_service.create(
                CriticalPageCreate(website_id=website_record.id, url=HttpUrl(critical_page_url))
            )
        self._db.expire(website_record, ["critical_pages"])

        self._link_recipients(website_record.id, model_create.recipient_emails)
        return WebsiteRead.model_validate(website_record)

    def _link_recipients(self, website_id: uuid.UUID, recipient_emails: Sequence[str]) -> None:
        """Links each email address to a website, adding it as a recipient first if it is new.

        An address given twice, or already linked to the website, is only linked once.

        Args:
            website_id: The website.
            recipient_emails: The email addresses to email about the website.
        """
        recipient_service = RecipientService(self._db)
        for recipient_email in dict.fromkeys(recipient_emails):
            try:
                recipient = recipient_service.get_by_email(recipient_email)
            except NotFoundError:
                recipient = recipient_service.create(RecipientCreate(email=recipient_email))
            recipient_service.link_recipient_and_website(website_id=website_id, recipient_id=recipient.id)

    def update(self, id: uuid.UUID, model_update: WebsiteUpdate) -> WebsiteRead:
        """Updates attributes of an existing website record and which recipients are emailed about it.

        Re-activating an inactive website clears why it was deactivated and its failed attempts, so one more rate
        limit does not switch it off again.

        Args:
            id: The UUID identifier of the website record to update.
            model_update: The WebsiteUpdate schema containing modified fields and the recipients to add or remove.

        Returns:
            The refreshed WebsiteRead data model instance following updates.

        Raises:
            NotFoundError: If the website record or a recipient being removed does not exist.
            IntegrityError: If updated attributes violate database constraints.
        """
        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)

        update_data = model_update.model_dump(
            exclude_unset=True,
            exclude={"add_recipient_emails", "remove_recipient_emails"},
        )
        if model_update.active and not website_record.active:
            update_data |= {"deactivated_reason": None, "failed_attempts_at_min_speed": 0}

        recipient_service = RecipientService(self._db)
        if model_update.add_recipient_emails:
            self._link_recipients(website_record.id, model_update.add_recipient_emails)

        if model_update.remove_recipient_emails:
            for recipient_email in model_update.remove_recipient_emails:
                recipient = recipient_service.get_by_email(recipient_email)
                recipient_service.unlink_recipient_and_website(website_id=website_record.id, recipient_id=recipient.id)

        if update_data:
            repository.update(self._db, record=website_record, updates=update_data)

        website_record: DBWebsite = repository.get(self._db, table=DBWebsite, id=id)
        return WebsiteRead.model_validate(website_record)

    def save_scan_result(self, id: uuid.UUID, scan_result: WebsiteScanResult) -> None:
        """Saves what a scan of a website found: each critical page as it is now, and the pages found on the website.

        What changed is not saved here, but in the scan's history (see `ScanRunService`).

        Args:
            id: The UUID identifier of the website that was scanned.
            scan_result: What the scan found.

        Raises:
            NotFoundError: If one of the critical pages does not exist.
            IntegrityError: If a page found on the website is already saved for it.
        """
        critical_page_service = CriticalPageService(self._db)
        for critical_page_id, page_result in (scan_result.critical_page_updates or {}).items():
            critical_page_service.update(id=critical_page_id, model_update=page_result.as_critical_page_update())

        internal_link_service = InternalLinkService(self._db)
        if scan_result.initial_internal_links:
            internal_link_service.create_batch(urls=scan_result.initial_internal_links, website_id=id)
        if scan_result.recent_added_internal_links:
            internal_link_service.create_batch(urls=scan_result.recent_added_internal_links, website_id=id)
        if scan_result.recent_removed_internal_links:
            internal_link_service.delete_batch(urls=scan_result.recent_removed_internal_links, website_id=id)

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
        on_cooldown_until = datetime.now(UTC) + timedelta(hours=hours)

        website_record = repository.update(
            self._db,
            record=repository.get(self._db, table=DBWebsite, id=id),
            updates=WebsiteUpdate(on_cooldown_until=on_cooldown_until).model_dump(exclude_unset=True),
        )
        logger.warning(f"Website {website_record.url} placed on cooldown until {on_cooldown_until}.")
        return WebsiteRead.model_validate(website_record)

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
