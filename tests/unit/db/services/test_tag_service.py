import uuid
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session

from app.db.migrations import WEBSITE_TAGS_VERSION, prepare_database
from app.db.schema import Base, DBRecipient, DBWebsite
from app.db.services.tag_service import TagService
from app.db.session import configure_sqlite_connection
from app.models.tag_models import TagName


def test_multiple_tags_and_assignments_survive_reopening_database(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'tags.db'}")
    event.listen(engine, "connect", configure_sqlite_connection)
    prepare_database(engine)
    with Session(engine) as session:
        website = DBWebsite(url="https://example.com/")
        session.add(website)
        session.flush()
        website_id = website.id
        service = TagService(session)
        news = service.save(TagName(name="News"))
        study = service.save(TagName(name="Study"))
        for tag in (news, study):
            service.set_assignment(website_id, tag.id, assigned=True)
        session.commit()
    engine.dispose()
    with Session(engine) as session:
        service = TagService(session)
        assert service.get_for_website(website_id) == [news, study]
        assert set(service.memberships()[website_id]) == {news.id, study.id}
    engine.dispose()


def test_categories_convert_to_tags_once_without_changing_saved_records(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old-categories.db'}")
    event.listen(engine, "connect", configure_sqlite_connection)
    old_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name
        not in {
            "website_tags",
            "website_tag_association",
        }
    ]
    Base.metadata.create_all(engine, tables=old_tables)
    with Session(engine) as session:
        website = DBWebsite(url="https://example.com/", recipients=[DBRecipient(email="test@example.com")])
        session.add(website)
        session.commit()
        website_id = website.id
    category_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=2")
        connection.exec_driver_sql(
            "CREATE TABLE website_categories (id CHAR(32) PRIMARY KEY, name VARCHAR NOT NULL, "
            "name_key VARCHAR NOT NULL UNIQUE, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE website_category_membership (website_id CHAR(32) PRIMARY KEY, category_id CHAR(32) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO website_categories VALUES (?, 'News', 'news', '2026-10-12 04:00:00', '2026-10-12 04:00:00')",
            (category_id.hex,),
        )
        connection.exec_driver_sql(
            "INSERT INTO website_category_membership VALUES (?, ?)",
            (website_id.hex, category_id.hex),
        )
        snapshots = {
            table.name: connection.exec_driver_sql(f'SELECT * FROM "{table.name}"').all() for table in old_tables
        }
    prepare_database(engine)
    prepare_database(engine)
    assert {"website_tags", "website_tag_association"} <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == WEBSITE_TAGS_VERSION
        assert {
            table.name: connection.exec_driver_sql(f'SELECT * FROM "{table.name}"').all() for table in old_tables
        } == snapshots
    with Session(engine) as session:
        service = TagService(session)
        assert service.get_for_website(website_id)[0].name == "News"
        assert service.memberships() == {website_id: [category_id]}
        service.delete(category_id)
        session.commit()
    # Old category rows must not resurrect a deleted tag on the next startup.
    prepare_database(engine)
    with Session(engine) as session:
        assert TagService(session).get_all() == []
        assert TagService(session).memberships() == {}
        assert session.get(DBWebsite, website_id) is not None
    engine.dispose()
