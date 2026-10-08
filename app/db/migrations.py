import logging
from datetime import UTC, datetime, tzinfo
from typing import Any

from sqlalchemy import Column, Connection, DateTime, Engine, MetaData, Table, column, inspect, select, table, update
from sqlalchemy.schema import CreateColumn

from app.db.schema import Base

logger: logging.Logger = logging.getLogger(__name__)

OUTDATED_UNIQUE_INDEXES: dict[str, str] = {"ix_internal_links_url": "internal_links"}

# Older versions of the app saved these times in the computer's own time zone. The others (`created_at` and
# `updated_at`) were always saved by SQLite, in UTC.
COLUMNS_SAVED_IN_LOCAL_TIME: dict[str, tuple[str, ...]] = {
    "websites": ("last_scan_at", "on_cooldown_until"),
    "recipients": ("last_email_at",),
    "scan_runs": ("scanned_at", "notified_at"),
}
# The database version (SQLite's `user_version`) from which every time is saved in UTC
UTC_TIMES_VERSION: int = 1


def _missing_columns(connection: Connection, table: Table) -> list[Column[Any]]:
    """Returns the columns of a table's model that are not in the database's copy of that table.

    Args:
        connection: An open connection to the database.
        table: The table as defined by the models.

    Returns:
        The columns to add, or an empty list if the table does not exist yet (`create_all()` makes it).
    """
    inspector = inspect(connection)
    if not inspector.has_table(table.name):
        return []

    existing_column_names: set[str] = {column["name"] for column in inspector.get_columns(table.name)}
    return [column for column in table.columns if column.name not in existing_column_names]


def _add_column(connection: Connection, table: Table, column: Column[Any]) -> None:
    """Adds a column to an existing table, giving the existing rows NULL (or the column's server default).

    Args:
        connection: An open connection to the database.
        table: The table to add the column to.
        column: The column to add, as defined by the models.
    """
    table_name: str = connection.dialect.identifier_preparer.format_table(table)
    column_definition = CreateColumn(column).compile(dialect=connection.dialect)
    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")
    logger.info(f"Added the {column.name} column to the {table.name} table.")


def add_missing_columns(engine: Engine, metadata: MetaData) -> None:
    """Adds columns that were added to the models after the user's database was created.

    `create_all()` only creates missing tables, so without this every query on a table that has
    gained a column would fail for anyone upgrading. Only suitable for columns that can be NULL
    or have a server default, as the rows already in the table have no value for them.

    Args:
        engine: The engine of the database to update.
        metadata: The metadata of the models the database should match.
    """
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            for column in _missing_columns(connection, table):
                _add_column(connection, table, column)


def drop_outdated_unique_indexes(engine: Engine, outdated_indexes: dict[str, str]) -> None:
    """Drops unique indexes that older versions of the app made but the models no longer have as unique.

    For example, older versions made each internal link unique across every website, so a page could not be saved for
    two websites that overlap. It is replaced by a unique index per website (see `add_missing_indexes`).

    Args:
        engine: The engine of the database to update.
        outdated_indexes: The name of each index to drop if it is unique, and the table it is on.
    """
    with engine.begin() as connection:
        inspector = inspect(connection)
        for index_name, table_name in outdated_indexes.items():
            if not inspector.has_table(table_name):
                continue
            if any(index["name"] == index_name and index["unique"] for index in inspector.get_indexes(table_name)):
                connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
                logger.info("Dropped the outdated unique index %s on the %s table.", index_name, table_name)


def add_missing_indexes(engine: Engine, metadata: MetaData) -> None:
    """Creates the indexes of the models that are not in the database yet.

    `create_all()` only creates the indexes of tables it creates, so an index added to an existing table's model
    would otherwise never be made.

    Args:
        engine: The engine of the database to update.
        metadata: The metadata of the models the database should match.
    """
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table in metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_index_names: set[str | None] = {index["name"] for index in inspector.get_indexes(table.name)}
            for index in table.indexes:
                if index.name not in existing_index_names:
                    index.create(connection)
                    logger.info("Added the %s index to the %s table.", index.name, table.name)


def _database_version(connection: Connection) -> int:
    """Reads the version number saved in the database, which says which one-off updates it has had.

    Args:
        connection: An open connection to the database.

    Returns:
        The version, which is 0 for a database that has never had one set.
    """
    return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def _local_time_to_utc(saved_time: datetime, local_time_zone: tzinfo | None) -> datetime:
    """Converts a time saved in the computer's own time zone to UTC, without a time zone, as times are now saved.

    Args:
        saved_time: The time as it was saved, without a time zone.
        local_time_zone: The time zone it was saved in, or None for the computer's own time zone.

    Returns:
        The same moment in UTC, e.g. 13:00 saved in Perth (UTC+8) becomes 05:00.
    """
    return saved_time.replace(tzinfo=local_time_zone).astimezone(UTC).replace(tzinfo=None)


def _convert_column_to_utc(
    connection: Connection, table_name: str, column_name: str, local_time_zone: tzinfo | None
) -> None:
    """Converts every time in one column from the computer's own time zone to UTC.

    The column is read as a plain date and time, not as the app's UTC column, so the times are read as they were saved.

    Args:
        connection: An open connection to the database.
        table_name: The table the column is in.
        column_name: The column of times to convert.
        local_time_zone: The time zone the times were saved in, or None for the computer's own time zone.
    """
    saved_times = table(table_name, column("id"), column(column_name, DateTime()))
    time_column = saved_times.c[column_name]
    rows = connection.execute(select(saved_times.c.id, time_column).where(time_column.is_not(None))).all()
    for row_id, saved_time in rows:
        connection.execute(
            update(saved_times)
            .where(saved_times.c.id == row_id)
            .values({column_name: _local_time_to_utc(saved_time, local_time_zone)})
        )


def convert_local_times_to_utc(
    engine: Engine,
    columns_by_table: dict[str, tuple[str, ...]],
    local_time_zone: tzinfo | None = None,
) -> None:
    """Converts the times an older version of the app saved in the computer's own time zone to UTC, once.

    The database's version is then set, so the times are never converted twice. A new database has nothing to
    convert, so it is only given the version.

    Args:
        engine: The engine of the database to update.
        columns_by_table: The columns that were saved in local time, by the table they are in.
        local_time_zone: The time zone the times were saved in, or None for the computer's own time zone.
    """
    with engine.begin() as connection:
        if _database_version(connection) >= UTC_TIMES_VERSION:
            return
        inspector = inspect(connection)
        for table_name, column_names in columns_by_table.items():
            if not inspector.has_table(table_name):
                continue
            for column_name in column_names:
                _convert_column_to_utc(connection, table_name, column_name, local_time_zone)
        connection.exec_driver_sql(f"PRAGMA user_version = {UTC_TIMES_VERSION}")
        logger.info("Any times saved in this computer's time zone were converted to UTC.")


def prepare_database(engine: Engine) -> None:
    """Creates any tables the database is missing, then brings a database made by an older version of the app up
    to date.

    Args:
        engine: The engine of the database to prepare.
    """
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine, Base.metadata)
    drop_outdated_unique_indexes(engine, OUTDATED_UNIQUE_INDEXES)
    add_missing_indexes(engine, Base.metadata)
    convert_local_times_to_utc(engine, COLUMNS_SAVED_IN_LOCAL_TIME)
