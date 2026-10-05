import logging
from typing import Any

from sqlalchemy import Column, Connection, Engine, MetaData, Table, inspect
from sqlalchemy.schema import CreateColumn

logger: logging.Logger = logging.getLogger(__name__)


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
