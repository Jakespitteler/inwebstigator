import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.db import repository
from app.db.errors import IntegrityError, NotFoundError
from tests.conftest import DBTestTable


def test_get(session: Session, test_record: DBTestTable) -> None:
    """
    Tests retrieving a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    fetched_record: DBTestTable = repository.get(session, table=DBTestTable, id=test_record.id)
    assert fetched_record.id == test_record.id
    assert fetched_record.name == test_record.name


def test_get_raises_not_found_error(session: Session) -> None:
    """
    Tests that retrieving a non-existent ID raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    invalid_id: uuid.UUID = uuid.uuid4()
    with pytest.raises(NotFoundError) as e:
        repository.get(session, table=DBTestTable, id=invalid_id)
    assert str(invalid_id) in str(e.value)


def test_get_list(session: Session) -> None:
    """
    Tests retrieving list records.

    Args:
        session: The database session fixture.
    """
    record1 = DBTestTable(name="Record A")
    record2 = DBTestTable(name="Record B")
    record3 = DBTestTable(name="Record C")
    for record in [record1, record2, record3]:
        repository.add(session, record)

    records: Sequence[DBTestTable] = repository.get_list(session, table=DBTestTable)
    assert len(records) == 3


def test_get_list_with_limit(session: Session) -> None:
    """
    Tests retrieving a list records with limit and skip boundaries.

    Args:
        session: The database session fixture.
    """
    record1 = DBTestTable(name="Record A")
    record2 = DBTestTable(name="Record B")
    record3 = DBTestTable(name="Record C")
    for record in [record1, record2, record3]:
        repository.add(session, record)

    # Retrieving with limit
    records = repository.get_list(session, table=DBTestTable, skip=0, limit=2)
    assert len(records) == 2


# TODO: Test invalid limits


def test_get_list_attributes(session: Session, test_record: DBTestTable) -> None:
    """
    Tests retrieving a list of records by their attributes.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    records: Sequence[DBTestTable] = repository.get_list(
        session,
        table=DBTestTable,
        attributes={DBTestTable.name.key: test_record.name},
    )
    assert len(records) == 1
    assert records[0].id == test_record.id


def test_add(session: Session) -> None:
    """
    Tests adding a new record.

    Args:
        session: The database session fixture.
    """
    record = DBTestTable(name="Create Record")

    repository.add(session, record)
    assert record.id is not None

    fetched_record: DBTestTable = repository.get(session, table=DBTestTable, id=record.id)
    assert fetched_record.name == record.name


def test_add_raises_integrity_error(session: Session, test_record: DBTestTable) -> None:
    """
    Tests that add raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
    """
    invalid_record = DBTestTable(name=test_record.name)
    with pytest.raises(IntegrityError) as e:
        repository.add(session, invalid_record)
    assert "unique constraint" in str(e.value)


def test_batch_add(session: Session) -> None:
    """
    Tests adding multiple records in batch.

    Args:
        session: The database session fixture.
    """
    records = [
        DBTestTable(name="Batch Record 1"),
        DBTestTable(name="Batch Record 2"),
        DBTestTable(name="Batch Record 3"),
    ]

    repository.batch_add(session, records)

    for record in records:
        assert record.id is not None
        fetched_record: DBTestTable = repository.get(session, table=DBTestTable, id=record.id)
        assert fetched_record.name == record.name


def test_batch_add_raises_integrity_error(session: Session, test_record: DBTestTable) -> None:
    """
    Tests that batch_add raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    records = [
        DBTestTable(name="Valid Batch Record"),
        DBTestTable(name=test_record.name),  # Duplicate name
    ]

    with pytest.raises(IntegrityError) as e:
        repository.batch_add(session, records)
    assert "unique constraint" in str(e.value)


def test_update(session: Session, test_record: DBTestTable) -> None:
    """
    Tests updating a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    updates: dict[str, str] = {DBTestTable.name.key: "Updated Record"}
    updated_record: DBTestTable = repository.update(session, test_record, updates)
    assert updated_record.name == updates[DBTestTable.name.key]

    # Confirm persistence
    session.expire(updated_record)
    fetched_updated_record: DBTestTable = repository.get(session, table=DBTestTable, id=test_record.id)
    assert fetched_updated_record.name == updates[DBTestTable.name.key]


def test_update_raises_integrity_error(session: Session, test_record: DBTestTable) -> None:
    """
    Tests that update raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    record = DBTestTable(name="Dummy Record")
    repository.add(session, record)

    invalid_updates: dict[str, str] = {DBTestTable.name.key: test_record.name}
    with pytest.raises(IntegrityError) as e:
        repository.update(session, record, invalid_updates)
    assert "unique constraint" in str(e.value)


def test_delete(session: Session, test_record: DBTestTable) -> None:
    """
    Tests deleting a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    repository.delete(session, table=DBTestTable, id=test_record.id)

    # Confirm it's gone
    with pytest.raises(NotFoundError):
        repository.get(session, table=DBTestTable, id=test_record.id)
