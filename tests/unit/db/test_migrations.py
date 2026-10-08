from collections.abc import Iterator

import pytest
from sqlalchemy import Column, Engine, Integer, MetaData, String, Table, create_engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.migrations import add_missing_columns, update_indexes
from app.db.schema import Base, DBInternalLink, DBWebsite


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


# ======================================
# update_indexes
# ======================================


def _indexes(engine: Engine, table_name: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    """Returns each index on a table as its columns and whether it is unique."""
    return {
        index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
        for index in inspect(engine).get_indexes(table_name)
    }


@pytest.fixture
def database_with_links_unique_across_websites() -> Iterator[Engine]:
    """Provides a database made by an older version of the app, where an internal link's URL had to be unique across
    every website rather than within its website, holding one website with one link."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_internal_link_url_website")
        connection.exec_driver_sql("DROP INDEX ix_internal_links_url")
        connection.exec_driver_sql("CREATE UNIQUE INDEX ix_internal_links_url ON internal_links (url)")

    with Session(engine) as session:
        website = DBWebsite(url="https://example.com")
        session.add(website)
        session.flush()
        session.add(DBInternalLink(url="https://example.com/news/story", website_id=website.id))
        session.commit()

    yield engine
    engine.dispose()


def test_update_indexes_lets_overlapping_websites_share_internal_links(
    database_with_links_unique_across_websites: Engine,
) -> None:
    """Tests upgrading makes links unique within their website, keeping existing links, so another website can save
    the same link while one website still cannot save it twice."""
    engine = database_with_links_unique_across_websites

    update_indexes(engine, Base.metadata)

    assert _indexes(engine, "internal_links") == {
        "ix_internal_links_url": (("url",), False),
        "uq_internal_link_url_website": (("url", "website_id"), True),
    }
    with Session(engine) as session:
        first_website = session.scalars(select(DBWebsite)).one()
        assert [link.url for link in first_website.internal_links] == ["https://example.com/news/story"]

        news_section = DBWebsite(url="https://example.com/news")
        session.add(news_section)
        session.flush()
        session.add(DBInternalLink(url="https://example.com/news/story", website_id=news_section.id))
        session.commit()

        session.add(DBInternalLink(url="https://example.com/news/story", website_id=news_section.id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_update_indexes_leaves_up_to_date_and_missing_tables_alone(
    database_with_links_unique_across_websites: Engine,
) -> None:
    """Tests running it again changes nothing, a database made by this version is already up to date, and tables not
    created yet are left for `create_all()` to make."""
    engine = database_with_links_unique_across_websites
    update_indexes(engine, Base.metadata)
    upgraded = _indexes(engine, "internal_links")

    update_indexes(engine, Base.metadata)
    assert _indexes(engine, "internal_links") == upgraded

    fresh_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(fresh_engine)
    update_indexes(fresh_engine, Base.metadata)
    assert _indexes(fresh_engine, "internal_links") == upgraded

    empty_engine = create_engine("sqlite:///:memory:")
    update_indexes(empty_engine, Base.metadata)
    assert inspect(empty_engine).get_table_names() == []
