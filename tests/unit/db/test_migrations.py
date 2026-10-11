from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy.exc
from sqlalchemy import Column, Engine, Index, Integer, MetaData, String, Table, create_engine, insert, inspect, select

from app.core.config import config
from app.db.migrations import (
    UTC_TIMES_VERSION,
    add_missing_columns,
    add_missing_indexes,
    convert_local_times_to_utc,
    drop_outdated_unique_indexes,
    prepare_database,
)


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


def test_prepare_database_lets_two_websites_save_the_same_page(tmp_path: Path) -> None:
    """Tests a database made when internal links had to be unique across every website is updated, so overlapping
    websites (e.g. example.com and example.com/research) can both save a page, but one website still cannot save it
    twice."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE internal_links (id CHAR(32) PRIMARY KEY, url VARCHAR, website_id CHAR(32))"
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX ix_internal_links_url ON internal_links (url)")

    prepare_database(engine)
    prepare_database(engine)  # Running it again changes nothing

    insert_link = "INSERT INTO internal_links (id, url, website_id) VALUES (?, 'https://example.com/a', ?)"
    with engine.begin() as connection:
        connection.exec_driver_sql(insert_link, ("1", "website-1"))
        connection.exec_driver_sql(insert_link, ("2", "website-2"))
    with pytest.raises(sqlalchemy.exc.IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql(insert_link, ("3", "website-1"))
    engine.dispose()


PERTH: timezone = timezone(timedelta(hours=8))


def _saved_scan_time(engine: Engine) -> str:
    """Reads the one scan's time exactly as it is saved in the database."""
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT scanned_at FROM scan_runs").scalar_one()


def test_times_saved_in_local_time_are_converted_to_utc_once(tmp_path: Path) -> None:
    """Tests a time an older version of the app saved in the computer's own time (Perth, UTC+8) is moved to UTC,
    and is not moved again when the app next starts."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE scan_runs (id CHAR(32) PRIMARY KEY, scanned_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO scan_runs VALUES ('1', '2026-10-08 13:00:00.000000')")

    convert_local_times_to_utc(engine, {"scan_runs": ("scanned_at",)}, local_time_zone=PERTH)
    convert_local_times_to_utc(engine, {"scan_runs": ("scanned_at",)}, local_time_zone=PERTH)

    assert _saved_scan_time(engine) == "2026-10-08 05:00:00.000000"
    engine.dispose()


def test_a_new_database_is_marked_as_saving_utc_times(tmp_path: Path) -> None:
    """Tests a new database is marked as already using UTC, so a time the app saves later is never converted."""
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    prepare_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO websites (id, url, recommended_delay, recommended_concurrent, days_between_scans, active, "
            "failed_attempts_at_min_speed) VALUES ('1', 'https://example.com', 0.5, 5, 1, 1, 0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO scan_runs (id, website_id, scanned_at, status) "
            "VALUES ('1', '1', '2026-10-08 05:00:00.000000', 'success')"
        )

    prepare_database(engine)

    assert _saved_scan_time(engine) == "2026-10-08 05:00:00.000000"
    engine.dispose()


def _index_names(engine: Engine, table_name: str) -> list[str | None]:
    """Lists the names of a table's indexes as the database has them."""
    return [index["name"] for index in inspect(engine).get_indexes(table_name)]


def test_drop_outdated_unique_indexes_keeps_an_index_that_is_not_unique_and_skips_missing_tables(
    tmp_path: Path,
) -> None:
    """Tests only a unique index with an outdated name is dropped, so a plain index of the same name is kept, and a
    table that does not exist yet is skipped rather than failing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE internal_links (id CHAR(32) PRIMARY KEY, url VARCHAR)")
        connection.exec_driver_sql("CREATE INDEX ix_internal_links_url ON internal_links (url)")

    drop_outdated_unique_indexes(engine, {"ix_internal_links_url": "internal_links", "ix_missing_url": "missing_table"})

    assert _index_names(engine, "internal_links") == ["ix_internal_links_url"]
    engine.dispose()


def test_add_missing_indexes_adds_new_indexes_to_existing_tables_only(
    old_database: Engine, current_metadata: MetaData
) -> None:
    """Tests an index added to the model of an existing table is created, while a table not created yet is left for
    `create_all()` to make, and running it again changes nothing."""
    Index("ix_websites_nickname", current_metadata.tables["websites"].c.nickname)
    Index("ix_new_table_id", current_metadata.tables["new_table"].c.id)
    add_missing_columns(old_database, current_metadata)

    add_missing_indexes(old_database, current_metadata)
    add_missing_indexes(old_database, current_metadata)

    assert _index_names(old_database, "websites") == ["ix_websites_nickname"]
    assert inspect(old_database).get_table_names() == ["websites"]


def _insert_website(engine: Engine, website_id: str, active: bool, reason: str | None, failed_attempts: int) -> None:
    """Saves a website directly, as an older version of the app would have left it."""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO websites (id, url, recommended_delay, recommended_concurrent, days_between_scans, active, "
            "deactivated_reason, failed_attempts_at_min_speed) VALUES (?, ?, 3, 1, 1, ?, ?, ?)",
            (website_id, f"https://{website_id}.example.com", active, reason, failed_attempts),
        )


def _deactivated_reasons(engine: Engine) -> dict[str, str | None]:
    """Reads why each website was switched off, by its ID."""
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT id, deactivated_reason FROM websites").all()
    return {website_id: reason for website_id, reason in rows}


def test_websites_an_older_version_switched_off_for_rate_limiting_are_given_that_reason_once(tmp_path: Path) -> None:
    """Tests an inactive website with no reason saved that had reached the most failed attempts is saved as switched
    off for rate limiting, while one switched off on the dashboard, one too large and an active one are left alone,
    and a website switched off after the update is never changed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    prepare_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version = {UTC_TIMES_VERSION}")  # Before reasons were saved
    max_failures = config.web_crawler_max_failed_attempts_at_min_speed
    _insert_website(engine, "rate-limited", active=False, reason=None, failed_attempts=max_failures)
    _insert_website(engine, "switched-off", active=False, reason=None, failed_attempts=0)
    _insert_website(engine, "too-large", active=False, reason="too_large", failed_attempts=max_failures)
    _insert_website(engine, "active", active=True, reason=None, failed_attempts=max_failures)

    prepare_database(engine)
    _insert_website(engine, "later", active=False, reason=None, failed_attempts=max_failures)
    prepare_database(engine)

    assert _deactivated_reasons(engine) == {
        "rate-limited": "rate_limited",
        "switched-off": None,
        "too-large": "too_large",
        "active": None,
        "later": None,
    }
    engine.dispose()


def test_prepare_database_indexes_the_website_of_each_link_and_critical_page_in_an_older_database(
    tmp_path: Path,
) -> None:
    """Tests a database made before links and critical pages were indexed by website gains those indexes, so
    counting or loading one website's pages does not read every website's."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE internal_links (id CHAR(32) PRIMARY KEY, url VARCHAR, website_id CHAR(32))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE critical_pages (id CHAR(32) PRIMARY KEY, url VARCHAR NOT NULL, website_id CHAR(32) NOT NULL)"
        )

    prepare_database(engine)

    assert "ix_internal_links_website_id" in _index_names(engine, "internal_links")
    assert "ix_critical_pages_website_id" in _index_names(engine, "critical_pages")
    engine.dispose()


def test_prepare_database_adds_new_columns_to_an_older_database_keeping_its_rows(tmp_path: Path) -> None:
    """Tests a critical pages table made before the failure columns existed gains them, with the existing page given
    the column's default of 0 failures and no failure reason."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE critical_pages (id CHAR(32) PRIMARY KEY, created_at DATETIME, updated_at DATETIME, "
            "url VARCHAR NOT NULL, website_id CHAR(32) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO critical_pages (id, url, website_id) VALUES ('1', 'https://example.com/', 'website-1')"
        )

    prepare_database(engine)

    with engine.connect() as connection:
        saved_page = connection.exec_driver_sql(
            "SELECT url, consecutive_failures, last_failure_reason FROM critical_pages"
        ).one()
    assert tuple(saved_page) == ("https://example.com/", 0, None)
    engine.dispose()


def test_converting_times_skips_missing_tables_and_empty_times(tmp_path: Path) -> None:
    """Tests a table an older database does not have is skipped, a time that was never set stays empty, and the
    database is still marked as converted."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE scan_runs (id CHAR(32) PRIMARY KEY, scanned_at DATETIME, notified_at DATETIME)"
        )
        connection.exec_driver_sql("INSERT INTO scan_runs VALUES ('1', '2026-10-08 13:00:00.000000', NULL)")

    convert_local_times_to_utc(
        engine, {"websites": ("last_scan_at",), "scan_runs": ("scanned_at", "notified_at")}, local_time_zone=PERTH
    )

    with engine.connect() as connection:
        saved_times = connection.exec_driver_sql("SELECT scanned_at, notified_at FROM scan_runs").one()
        database_version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    assert tuple(saved_times) == ("2026-10-08 05:00:00.000000", None)
    assert database_version == UTC_TIMES_VERSION
    engine.dispose()


def test_times_are_converted_from_the_computers_own_time_zone_by_default(tmp_path: Path) -> None:
    """Tests a saved time is read as the computer's own local time when no time zone is given."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE scan_runs (id CHAR(32) PRIMARY KEY, scanned_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO scan_runs VALUES ('1', '2026-10-08 13:00:00.000000')")

    convert_local_times_to_utc(engine, {"scan_runs": ("scanned_at",)})

    local_time = datetime(2026, 10, 8, 13, 0).astimezone()  # 13:00 in the computer's own time zone
    expected_utc_time = local_time.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert _saved_scan_time(engine) == expected_utc_time
    engine.dispose()


def test_prepare_database_creates_the_folder_the_database_is_kept_in(tmp_path: Path) -> None:
    """Tests a database in a folder that does not exist yet (e.g. on a new computer) is still created."""
    db_path: Path = tmp_path / "new folder" / "inwebstigator.db"

    prepare_database(create_engine(f"sqlite:///{db_path.as_posix()}"))

    assert db_path.is_file()
