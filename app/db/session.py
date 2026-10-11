import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.config import config

BUSY_TIMEOUT_MILLISECONDS: int = 30_000


def configure_sqlite_connection(dbapi_connection: sqlite3.Connection, _connection_record: ConnectionPoolEntry) -> None:
    """Sets up each new connection to the SQLite database.

    - Foreign keys are checked, so deleting a website also deletes what belongs to it (SQLite leaves them off).
    - Write-ahead logging lets the dashboard read while a scan is saving.
    - A connection waits for a busy database, rather than failing straight away with "database is locked".

    Args:
        dbapi_connection: The new connection.
        _connection_record: The pool's record of the connection (unused).
    """
    cursor: sqlite3.Cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
    cursor.close()


engine: Engine = create_engine(url=config.db_url, connect_args={"check_same_thread": False})
event.listen(engine, "connect", configure_sqlite_connection)
SessionLocal: sessionmaker[Session] = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db_session() -> Generator[Session]:
    """
    Unit of Work Dependency:
    Controls the database transaction for the entire request lifecycle.
    """
    db_session: Session = SessionLocal()
    try:
        yield db_session
        db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()


db_context = contextmanager(get_db_session)
