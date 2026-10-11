import logging
import uuid
from collections.abc import Sequence

from pydantic import HttpUrl
from sqlalchemy import Delete, Select, delete, select

from app.db import repository
from app.db.schema import DBInternalLink
from app.db.services.base_crud_service import BaseCRUDService
from app.models.internal_link_models import (
    InternalLinkCreate,
    InternalLinkRead,
    InternalLinkUpdate,
)

logger: logging.Logger = logging.getLogger(__name__)

URLS_PER_DELETE: int = 500


class InternalLinkService(BaseCRUDService[DBInternalLink, InternalLinkRead, InternalLinkCreate, InternalLinkUpdate]):
    """Reads and writes the pages found on each website, in bulk where a website has many."""

    table = DBInternalLink
    read_model = InternalLinkRead

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
