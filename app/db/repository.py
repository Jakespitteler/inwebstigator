import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, insert, select
from sqlalchemy.exc import IntegrityError as SQLIntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload
from sqlalchemy.orm.interfaces import ORMOption

from app.core.errors import IntegrityError, NotFoundError
from app.db.schema import Base

logger: logging.Logger = logging.getLogger(__name__)


def get[DBTable: Base](
    session: Session,
    table: type[DBTable],
    id: uuid.UUID,
    relations: list[InstrumentedAttribute[Any]] | None = None,
) -> DBTable:
    """
    Retrieves a single record by its primary key with optional eager loading.

    Args:
        session: The database session.
        table: The table to query.
        id: The id of the record to retrieve.
        relations: Optional relational table attributes to eager load.

    Raises:
        NotFoundError: If no record exists with the provided ID.

    Returns:
        The record instance if found.
    """
    options: Sequence[ORMOption] = []
    if relations:
        options: Sequence[ORMOption] = [selectinload(relation) for relation in relations]
    record: DBTable | None = session.get(entity=table, ident=id, options=options)
    if not record:
        raise NotFoundError(id=id)
    logger.info(f"Record retrieved successfully. {record.__tablename__=}, {record.id=}")
    return record


def get_list[DBTable: Base](
    session: Session,
    table: type[DBTable],
    skip: int = 0,
    limit: int | None = 100,
    attributes: dict[str, Any] | None = None,
    relations: list[InstrumentedAttribute[Any]] | None = None,
) -> Sequence[DBTable]:
    """
    Retrieves records from a table.

    Args:
        session: The database session.
        table: The table to query.
        skip: The number of records to skip (offset)
        limit: The maximum number of records to return, or None to return every record
        attributes: Optional filtering criteria.
        relations: Optional relational table attributes to eager load.

    Returns:
        The retrieved records.
    """
    statement: Select[DBTable] = select(table)
    if attributes:
        statement = statement.filter_by(**attributes)
    statement = statement.offset(skip).limit(limit)
    if relations:
        options: Sequence[ORMOption] = [selectinload(relation) for relation in relations]
        statement = statement.options(*options)
    records: Sequence[DBTable] = session.scalars(statement).all()
    logger.info(f"Records retrieved successfully. {table.__tablename__=}")
    return records


def add(session: Session, record: Base) -> None:
    """
    Adds a new record to the table.

    Args:
        session: The database session.
        record: The record to add.

    Raises:
        IntegrityError: If record violates unique constraints. The session's unit of work rolls the transaction back.
    """
    try:
        session.add(record)
        session.flush()
    except SQLIntegrityError as e:
        logger.error(f"Failed to add record to database. {record.__tablename__=}, {record.id=}")
        raise IntegrityError() from e

    session.refresh(record)
    logger.info(f"Records added to database successfully. {record.__tablename__=}, {record.id=}")


def bulk_insert[DBTable: Base](session: Session, table: type[DBTable], rows: Sequence[dict[str, Any]]) -> None:
    """
    Inserts many rows into a table at once, without loading each one back as a record.

    Much faster than adding each row as a record for tens of thousands of rows (e.g. a large website's internal
    links), which would otherwise each be read back from the database after saving.

    Args:
        session: The database session.
        table: The table to insert into.
        rows: The column values of each row to insert.

    Raises:
        IntegrityError: If any row violates unique constraints. The session's unit of work rolls the transaction back.
    """
    if not rows:
        return
    try:
        session.execute(insert(table), list(rows))
    except SQLIntegrityError as e:
        logger.error(f"Failed to bulk insert rows into database. {table.__name__=}")
        raise IntegrityError() from e

    logger.info(f"Successfully bulk inserted {len(rows)} rows into database. {table.__name__=}")


def update[DBTable: Base](session: Session, record: DBTable, updates: dict[str, Any]) -> DBTable:
    """
    Updates the fields of an existing record.

    Args:
        session: The database session.
        record: The record to update.
        updates: The new values to apply.

    Raises:
        IntegrityError: If updates violate unique constraints. The session's unit of work rolls the transaction back.

    Returns:
        The updated record.
    """
    for key, value in updates.items():
        if hasattr(record, key):
            setattr(record, key, value)

    try:
        session.flush()
    except SQLIntegrityError as e:
        # Only the names of the fields are logged, as their values can be huge (e.g. a page's whole HTML)
        logger.error(f"Failed to update record in database. {record=}, fields={sorted(updates)}")
        raise IntegrityError() from e

    session.refresh(record)
    logger.info(
        f"Record updated in database successfully: {record.__tablename__=}, {record.id=} fields={sorted(updates)}"
    )
    return record


def delete[DBTable: Base](session: Session, table: type[DBTable], id: uuid.UUID) -> None:
    """
    Deletes a record by its primary key.

    Args:
        session: The database session.
        model: The table to query.
        id: The id of the record to delete.

    Raises:
        NotFoundError: If no record exists with the provided ID.
    """
    record: DBTable = get(session, table, id)
    session.delete(record)
    session.flush()
    logger.info(f"Record deleted from database successfully: {record.__tablename__=}, {record.id=}")
