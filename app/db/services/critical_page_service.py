import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import repository
from app.db.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.db.schema import DBCriticalPage
from app.db.utils.interfaces import CRUDService

logger: logging.Logger = logging.getLogger(__name__)


class CriticalPageService(CRUDService[CriticalPageRead, CriticalPageCreate, CriticalPageUpdate]):
    def __init__(self, session: Session):
        """_summary_

        Args:
            session (Session): The database session.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[CriticalPageRead]:
        """
        Retrieves critical_page records.

        Args:
            skip: The number of records to skip.
            limit: The maximum number of records to return.

        Returns:
            The retrieved critical_pages.
        """
        critical_page_records: Sequence[DBCriticalPage] = repository.get_list(
            self._db,
            table=DBCriticalPage,
            skip=skip,
            limit=limit,
        )
        return [CriticalPageRead.model_validate(critical_page_record) for critical_page_record in critical_page_records]

    def get(self, id: uuid.UUID) -> CriticalPageRead:
        """
        Retrieves a single critical_page by its primary key.

        Args:
            id: The id of the critical_page to retrieve.

        Raises:
            NotFoundError: If no critical_page exists with the provided ID.

        Returns:
            The retrieved critical_page.
        """
        critical_page_record: DBCriticalPage = repository.get(self._db, table=DBCriticalPage, id=id)
        return CriticalPageRead.model_validate(critical_page_record)

    def create(self, model_create: CriticalPageCreate) -> CriticalPageRead:
        """
        Creates a new critical_page record.

        Args:
            model_create: The critical_page details to create.

        Raises:
            IntegrityError: If the critical_page already exists in db.

        Returns:
            The critical_page record.
        """
        critical_page_record: DBCriticalPage = DBCriticalPage(**model_create.model_dump())
        repository.add(self._db, record=critical_page_record)
        return CriticalPageRead.model_validate(critical_page_record)

    def update(self, id: uuid.UUID, model_update: CriticalPageUpdate) -> CriticalPageRead:
        """
        Updates an existing critical_page record.

        Args:
            id: The id of the critical_page to update.
            model_update: The new data to apply to the critical_page.

        Raises:
            NotFoundError: If the critical_page with id does not exist.
            IntegrityError: If the critical_page updated details already exists in db.

        Returns:
            The updated critical_page.
        """
        critical_page_record: DBCriticalPage = repository.get(self._db, table=DBCriticalPage, id=id)
        critical_page_record = repository.update(
            self._db, record=critical_page_record, updates=model_update.model_dump(exclude_unset=True)
        )
        return CriticalPageRead.model_validate(critical_page_record)

    def delete(self, id: uuid.UUID) -> None:
        """
        Deletes a critical_page by its primary key.

        Args:
            id: The id of the critical_page to delete.

        Raises:
            NotFoundError: If no critical_page exists with the provided ID.
        """
        repository.get(self._db, table=DBCriticalPage, id=id)
        repository.delete(self._db, table=DBCriticalPage, id=id)
