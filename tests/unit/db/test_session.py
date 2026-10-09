from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import HttpUrl
from sqlalchemy import Engine, Table, create_engine, event, func, inspect, select
from sqlalchemy.orm import InstanceState

from app.core.errors import IntegrityError
from app.db import repository
from app.db import session as session_module
from app.db.migrations import prepare_database
from app.db.schema import Base, DBCriticalPage, DBRecipient, DBWebsite, website_recipient_association
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.session import BUSY_TIMEOUT_MILLISECONDS, SessionLocal, configure_sqlite_connection, db_context
from app.models.recipient_models import RecipientCreate
from app.models.website_models import WebsiteCreate


@pytest.fixture
def app_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Points the app's own sessions at a new database in a temporary folder, set up as the app sets up its own.

    Yields:
        The engine of the temporary database, for reading what the app saved from a separate connection.
    """
    engine: Engine = create_engine(f"sqlite:///{tmp_path / 'inwebstigator.db'}")
    event.listen(engine, "connect", configure_sqlite_connection)
    prepare_database(engine)
    monkeypatch.setitem(SessionLocal.kw, "bind", engine)
    yield engine
    engine.dispose()


def _saved_count(engine: Engine, table: type[Base] | Table) -> int:
    """Counts a table's rows from a new connection, so only what has been committed is counted."""
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table)) or 0


@pytest.mark.parametrize(
    ("pragma", "expected_value"),
    [("foreign_keys", 1), ("journal_mode", "wal"), ("busy_timeout", BUSY_TIMEOUT_MILLISECONDS)],
    ids=["foreign-keys-checked", "write-ahead-logging", "waits-for-a-busy-database"],
)
def test_connections_to_the_app_database_are_configured(pragma: str, expected_value: int | str) -> None:
    """Tests each connection to the app's database checks foreign keys, uses write-ahead logging and waits for a
    busy database rather than failing straight away."""
    with session_module.engine.connect() as connection:
        assert connection.exec_driver_sql(f"PRAGMA {pragma}").scalar_one() == expected_value


def test_changes_are_only_saved_when_the_unit_of_work_ends(app_database: Engine) -> None:
    """Tests services only flush, so their changes are not saved while the unit of work is open, and are saved once
    it ends without an error."""
    with db_context() as session:
        WebsiteService(session).create(
            WebsiteCreate(url=HttpUrl("https://example.com"), recipient_emails=["jj@example.com"])
        )
        assert _saved_count(app_database, DBWebsite) == 0

    assert _saved_count(app_database, DBWebsite) == 1
    assert _saved_count(app_database, DBRecipient) == 1


def test_an_error_undoes_every_change_in_the_unit_of_work(app_database: Engine) -> None:
    """Tests an error part way through a unit of work undoes everything saved before it, across services, and is
    passed on to the caller."""
    with pytest.raises(IntegrityError), db_context() as session:
        WebsiteService(session).create(
            WebsiteCreate(url=HttpUrl("https://example.com"), recipient_emails=["jj@example.com"])
        )
        RecipientService(session).create(RecipientCreate(email="jj@example.com"))  # Already a recipient

    for table in (DBWebsite, DBCriticalPage, DBRecipient, website_recipient_association):
        assert _saved_count(app_database, table) == 0


def test_the_session_is_closed_when_the_unit_of_work_ends(app_database: Engine) -> None:
    """Tests the unit of work closes its session, so the records it saved are no longer tied to it."""
    recipient = DBRecipient(email="jj@example.com")
    with db_context() as session:
        repository.add(session, recipient)

    recipient_state: InstanceState[DBRecipient] = inspect(recipient)
    assert recipient_state.detached
