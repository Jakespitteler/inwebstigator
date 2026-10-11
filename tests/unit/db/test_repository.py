import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import IntegrityError, NotFoundError
from app.db import repository
from app.db.schema import DBWebsite
from app.models.website_models import WebsiteRead
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


def test_get_list_without_a_limit_returns_every_record(session: Session) -> None:
    """
    Tests a limit of None returns every record, rather than the default first 100.

    Args:
        session: The database session fixture.
    """
    repository.bulk_insert(session, table=DBTestTable, rows=[{"name": f"Record {number}"} for number in range(101)])

    assert len(repository.get_list(session, table=DBTestTable)) == 100
    assert len(repository.get_list(session, table=DBTestTable, limit=None)) == 101


def test_get_list_invalid_limits(session: Session) -> None:
    """
    Tests retrieving list with negative or zero limits/skips.

    Args:
        session: The database session fixture.
    """
    record = DBTestTable(name="Limit Test Record")
    repository.add(session, record)

    # Limit 0 returns empty list
    records = repository.get_list(session, table=DBTestTable, skip=0, limit=0)
    assert len(records) == 0

    # Large skip out of bounds returns empty list
    records = repository.get_list(session, table=DBTestTable, skip=100, limit=10)
    assert len(records) == 0


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


def test_bulk_insert(session: Session) -> None:
    """
    Tests many rows are inserted at once, with their defaults (such as the ID) filled in.

    Args:
        session: The database session fixture.
    """
    repository.bulk_insert(session, table=DBTestTable, rows=[{"name": f"Bulk {number}"} for number in range(1_000)])

    records = repository.get_list(session, table=DBTestTable, limit=None)
    assert {record.name for record in records} == {f"Bulk {number}" for number in range(1_000)}
    assert all(record.id is not None for record in records)


def test_bulk_insert_with_no_rows_inserts_nothing(session: Session) -> None:
    """Tests inserting no rows does nothing, rather than failing or inserting an empty row."""
    repository.bulk_insert(session, table=DBTestTable, rows=[])

    assert repository.get_list(session, table=DBTestTable, limit=None) == []


def test_bulk_insert_raises_integrity_error(session: Session, test_record: DBTestTable) -> None:
    """
    Tests that bulk_insert raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    rows = [{"name": "Valid Bulk Row"}, {"name": test_record.name}]  # Duplicate name

    with pytest.raises(IntegrityError) as e:
        repository.bulk_insert(session, table=DBTestTable, rows=rows)
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


@pytest.mark.parametrize("relations", [[DBWebsite.critical_pages], None], ids=["asked-for", "not-asked-for"])
def test_get_list_loads_the_relations_asked_for(
    session: Session, test_website: WebsiteRead, relations: list[InstrumentedAttribute[Any]] | None
) -> None:
    """Tests the relations asked for are loaded with the records, so reading them does not query each record again,
    while relations not asked for are left to load when they are read."""
    session.expire_all()

    [website] = repository.get_list(session, table=DBWebsite, attributes={"id": test_website.id}, relations=relations)

    assert ("critical_pages" in inspect(website).unloaded) is (relations is None)
