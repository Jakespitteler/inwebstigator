import uuid
from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import repository
from app.db.schema import Base
from app.db.services.crud_protocol import CRUDService


class BaseCRUDService[DBTable: Base, ReadModel: BaseModel, CreateModel: BaseModel, UpdateModel: BaseModel](
    CRUDService[ReadModel, CreateModel, UpdateModel]
):
    """The standard operations on one table (get all, get, create, update and delete), shared by each table's service.

    A service only names its table and the model its records are read as. It adds its own queries, and overrides
    an operation that does more for its table (e.g. creating a website also creates its critical pages).

    Type Parameters:
        DBTable: The table the service reads and writes.
        ReadModel: The model each record is returned as.
        CreateModel: The model accepted when creating a record.
        UpdateModel: The model accepted when updating a record. Only the fields that were set are saved.

    Attributes:
        table: The table, set by each service.
        read_model: The model each record is returned as, set by each service.
    """

    table: type[DBTable]
    read_model: type[ReadModel]

    def __init__(self, session: Session) -> None:
        """Initialises the service with an active database session.

        Args:
            session: The SQLAlchemy session. Its unit of work saves the service's changes, or undoes them on an error.
        """
        self._db: Session = session

    def get_all(self, skip: int = 0, limit: int | None = 100) -> Sequence[ReadModel]:
        """Retrieves a page of the table's records.

        Args:
            skip: The number of records to skip, for paging. Defaults to 0.
            limit: The most records to return, or None for every record. Defaults to 100.

        Returns:
            The records, as read models.
        """
        records: Sequence[DBTable] = repository.get_list(self._db, table=self.table, skip=skip, limit=limit)
        return [self.read_model.model_validate(record) for record in records]

    def get(self, id: uuid.UUID) -> ReadModel:
        """Retrieves one record by its ID.

        Args:
            id: The ID of the record.

        Returns:
            The record, as a read model.

        Raises:
            NotFoundError: If no record has the ID.
        """
        record: DBTable = repository.get(self._db, table=self.table, id=id)
        return self.read_model.model_validate(record)

    def create(self, model_create: CreateModel) -> ReadModel:
        """Creates a record from a create model, with one column for each of the model's fields.

        Args:
            model_create: The new record's values.

        Returns:
            The record as it was saved, as a read model.

        Raises:
            IntegrityError: If the record breaks a database constraint, e.g. it already exists.
        """
        record: DBTable = self.table(**model_create.model_dump())
        repository.add(self._db, record=record)
        return self.read_model.model_validate(record)

    def update(self, id: uuid.UUID, model_update: UpdateModel) -> ReadModel:
        """Updates a record with the fields that were set on an update model.

        Args:
            id: The ID of the record.
            model_update: The new values. Fields that were not set are left as they are.

        Returns:
            The updated record, as a read model.

        Raises:
            NotFoundError: If no record has the ID.
            IntegrityError: If the new values break a database constraint.
        """
        record: DBTable = repository.get(self._db, table=self.table, id=id)
        updated_record: DBTable = repository.update(
            self._db, record=record, updates=model_update.model_dump(exclude_unset=True)
        )
        return self.read_model.model_validate(updated_record)

    def delete(self, id: uuid.UUID) -> None:
        """Deletes a record by its ID.

        Args:
            id: The ID of the record.

        Raises:
            NotFoundError: If no record has the ID.
        """
        repository.delete(self._db, table=self.table, id=id)
