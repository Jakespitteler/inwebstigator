from collections.abc import Iterator

import pytest
from sqlalchemy import Column, Engine, Integer, MetaData, String, Table, create_engine, insert, inspect, select

from app.db.migrations import add_missing_columns


@pytest.fixture
def old_database() -> Iterator[Engine]:
    """Provides a database made by an older version of the app, whose websites table has no `nickname` column."""
    engine = create_engine("sqlite:///:memory:")
    old_metadata = MetaData()
    old_websites = Table("websites", old_metadata, Column("id", Integer, primary_key=True), Column("url", String))
    old_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(old_websites).values(id=1, url="https://example.com"))

    yield engine
    engine.dispose()


@pytest.fixture
def current_metadata() -> MetaData:
    """Provides the current models, where the websites table has gained a `nickname` column and a new table."""
    metadata = MetaData()
    Table(
        "websites",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("url", String),
        Column("nickname", String, nullable=True),
    )
    Table("new_table", metadata, Column("id", Integer, primary_key=True))
    return metadata


def test_add_missing_columns_adds_new_columns_to_existing_tables(
    old_database: Engine, current_metadata: MetaData
) -> None:
    """Tests a column added to the models is added to an older database, with existing rows kept and given NULL."""
    add_missing_columns(old_database, current_metadata)

    column_names = [column["name"] for column in inspect(old_database).get_columns("websites")]
    assert column_names == ["id", "url", "nickname"]

    websites = current_metadata.tables["websites"]
    with old_database.connect() as connection:
        rows = connection.execute(select(websites.c.url, websites.c.nickname)).all()
    assert [tuple(row) for row in rows] == [("https://example.com", None)]


def test_add_missing_columns_leaves_up_to_date_and_missing_tables_alone(
    old_database: Engine, current_metadata: MetaData
) -> None:
    """Tests running it again changes nothing, and tables not created yet are left for `create_all()` to make."""
    add_missing_columns(old_database, current_metadata)
    add_missing_columns(old_database, current_metadata)

    assert inspect(old_database).get_table_names() == ["websites"]
    assert len(inspect(old_database).get_columns("websites")) == 3
