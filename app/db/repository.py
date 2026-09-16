import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
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
    limit: int = 100,
    attributes: dict[str, Any] | None = None,
    relations: list[InstrumentedAttribute[Any]] | None = None,
) -> Sequence[DBTable]:
    """
    Retrieves records from a table.

    Args:
        session: The database session.
        table: The table to query.
        skip: The number of records to skip (offset)
        limit: The maximum number of records to return
        attributes: Optional filtering criteria.
        relations: Optional relational table attributes to eager load.

    Returns:
        The retrieved records.
    """
    statement: Select[tuple[DBTable]] = select(table)
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
        IntegrityError: If record violates unique constraints.
    """
    try:
        session.add(record)
        session.flush()
    except SQLIntegrityError as e:
        logger.error(f"Failed to add record to database, rolling back. {record.__tablename__=}, {record.id=}")
        session.rollback()
        raise IntegrityError() from e

    session.refresh(record)
    logger.info(f"Records added to database successfully. {record.__tablename__=}, {record.id=}")


def batch_add[DBTable: Base](session: Session, records: Sequence[DBTable]) -> None:
    """
    Adds multiple new records to the table in batch.

    Args:
        session: The database session.
        records: The sequence of records to add.

    Raises:
        IntegrityError: If any record violates unique constraints.
    """
    try:
        session.add_all(records)
        session.flush()
    except SQLIntegrityError as e:
        tablename = records[0].__tablename__ if records else "unknown"
        logger.error(f"Failed to bulk add records to database, rolling back. {tablename=}")
        session.rollback()
        raise IntegrityError() from e

    for record in records:
        session.refresh(record)

    tablename = records[0].__tablename__ if records else "unknown"
    logger.info(f"Successfully bulk added {len(records)} records to database. {tablename=}")


def update[DBTable: Base](session: Session, record: DBTable, updates: dict[str, Any]) -> DBTable:
    """
    Updates the fields of an existing record.

    Args:
        session: The database session.
        record: The record to update.
        updates: The new values to apply.

    Raises:
        IntegrityError: If updates violate unique constraints.

    Returns:
        The updated record.
    """
    [setattr(record, key, value) for key, value in updates.items() if hasattr(record, key)]

    try:
        session.flush()
    except SQLIntegrityError as e:
        logger.error(f"Failed to update record in database, rolling back. {record=}, {updates=}")
        session.rollback()
        raise IntegrityError() from e

    session.refresh(record)
    logger.info(f"Record updated in database successfully: {record.__tablename__=}, {record.id=} {updates=}")
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


def batch_delete[DBTable: Base](
    session: Session,
    table: type[DBTable],
    ids: Sequence[uuid.UUID] | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """
    Deletes multiple records matching the provided IDs and/or attribute criteria.

    Args:
        session: The database session.
        table: The table to query.
        ids: Optional sequence of IDs to match.
        attributes: Optional attribute-value pairs to filter by. Supports sequence values for SQL IN clauses.

    Raises:
        NotFoundError: If `ids` are explicitly passed but any provided ID is missing.
        ValueError: If neither `ids` nor `attributes` are provided.
        IntegrityError: If deletion violates foreign key constraints.
    """
    if not ids and not attributes:
        raise ValueError("At least one search parameter ('ids' or 'attributes') must be provided.")

    statement: Select[tuple[DBTable]] = select(table)

    if ids:
        statement = statement.where(table.id.in_(ids))

    if attributes:
        for key, value in attributes.items():
            column = getattr(table, key)
            if isinstance(value, (list, tuple, set)):
                statement = statement.where(column.in_(value))
            else:
                statement = statement.where(column == value)

    records = session.scalars(statement).all()

    # If explicit IDs were requested, ensure all were found
    if ids:
        found_ids = {record.id for record in records}
        missing_ids = set(ids) - found_ids
        if missing_ids:
            missing_id = next(iter(missing_ids))
            raise NotFoundError(id=missing_id)

    if not records:
        logger.info(f"No records found to delete for {table.__tablename__=}.")
        return

    try:
        for record in records:
            session.delete(record)
        session.flush()
    except SQLIntegrityError as e:
        tablename = getattr(table, "__tablename__", "unknown")
        logger.error(f"Failed to bulk delete records from database, rolling back. {tablename=}")
        session.rollback()
        raise IntegrityError() from e

    tablename = records[0].__tablename__
    logger.info(f"Successfully bulk deleted {len(records)} records from database. {tablename=}")
