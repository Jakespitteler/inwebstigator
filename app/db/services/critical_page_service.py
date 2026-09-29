import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import repository
from app.db.schema import DBCriticalPage
from app.db.utils.interfaces import CRUDService
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate

logger: logging.Logger = logging.getLogger(__name__)


class CriticalPageService(CRUDService[CriticalPageRead, CriticalPageCreate, CriticalPageUpdate]):
    def __init__(self, session: Session):
        """Initialises the CriticalPageService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[CriticalPageRead]:
        """Retrieves a paginated list of critical page records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of CriticalPageRead models representing the retrieved records.
        """
        critical_page_records: Sequence[DBCriticalPage] = repository.get_list(
            self._db,
            table=DBCriticalPage,
            skip=skip,
            limit=limit,
        )
        return [CriticalPageRead.model_validate(critical_page_record) for critical_page_record in critical_page_records]

    def get(self, id: uuid.UUID) -> CriticalPageRead:
        """Retrieves a single critical page record by its unique primary key identifier.

        Args:
            id: The UUID identifier of the target critical page record.

        Returns:
            The matching CriticalPageRead data model instance.

        Raises:
            NotFoundError: If no critical page record matches the provided UUID.
        """
        critical_page_record: DBCriticalPage = repository.get(self._db, table=DBCriticalPage, id=id)
        return CriticalPageRead.model_validate(critical_page_record)

    def create(self, model_create: CriticalPageCreate) -> CriticalPageRead:
        """Updates attributes of an existing critical page record by its primary key.

        Args:
            id: The UUID identifier of the critical page record to update.
            model_update: The CriticalPageUpdate schema containing fields to update.

        Returns:
            The updated CriticalPageRead data model instance.

        Raises:
            NotFoundError: If no critical page record matches the provided UUID.
            IntegrityError: If updated attribute values violate database constraints.
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
        """Deletes a critical page record from the database by its primary key.

        Args:
            id: The UUID identifier of the critical page record to remove.

        Raises:
            NotFoundError: If no critical page record matches the provided UUID.
        """
        repository.get(self._db, table=DBCriticalPage, id=id)
        repository.delete(self._db, table=DBCriticalPage, id=id)
