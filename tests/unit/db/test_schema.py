import json
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy import select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.db import repository
from app.db.schema import DBCriticalPage, DBInternalLink, DBWebsite, new_id

PERTH: timezone = timezone(timedelta(hours=8))


def test_times_are_saved_in_utc_and_read_back_as_utc(session: Session) -> None:
    """Tests a time in another time zone is saved as UTC, and read back as the same moment, marked as UTC."""
    website = DBWebsite(url="https://example.com", last_scan_at=datetime(2026, 10, 8, 13, 0, tzinfo=PERTH))
    session.add(website)
    session.flush()

    saved_time: str = session.execute(text("SELECT last_scan_at FROM websites")).scalar_one()
    session.expire(website)

    assert saved_time == "2026-10-08 05:00:00.000000"
    assert website.last_scan_at == datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
    assert website.last_scan_at is not None and website.last_scan_at.tzinfo is UTC


def test_a_time_without_a_time_zone_is_not_saved(session: Session) -> None:
    """Tests a time without a time zone is refused, as it is not known which time zone it is in."""
    session.add(DBWebsite(url="https://example.com", last_scan_at=datetime(2026, 10, 8, 13, 0)))

    with pytest.raises(StatementError, match="has no time zone"):
        session.flush()


def test_times_are_compared_in_utc_when_searching(session: Session) -> None:
    """Tests a time in another time zone used in a search is turned into UTC first, so it matches the same moment."""
    session.add(DBWebsite(url="https://example.com", last_scan_at=datetime(2026, 10, 8, 5, 0, tzinfo=UTC)))
    session.flush()

    same_moment_in_perth = datetime(2026, 10, 8, 13, 0, tzinfo=PERTH)
    found = session.scalars(select(DBWebsite).where(DBWebsite.last_scan_at == same_moment_in_perth)).all()

    assert [website.url for website in found] == ["https://example.com"]


def test_created_at_is_the_current_time_in_utc(session: Session) -> None:
    """Tests the time a record is created is saved by the database in UTC, and read back as UTC, whatever the
    computer's own time zone is."""
    website = DBWebsite(url="https://example.com")
    session.add(website)
    session.flush()
    session.refresh(website)

    assert website.created_at.tzinfo is UTC
    assert abs(website.created_at - datetime.now(UTC)) < timedelta(minutes=1)


def test_urls_and_patterns_are_saved_as_the_text_they_were_written_as(session: Session) -> None:
    """Tests a pydantic URL and a regular expression are saved as plain text, not as their Python form."""
    website = DBWebsite(url=HttpUrl("https://example.com/"))
    session.add(website)
    session.flush()
    session.add(
        DBCriticalPage(
            url=HttpUrl("https://example.com/fees"),
            website_id=website.id,
            links=[HttpUrl("https://example.com/apply")],
            ignore_rules=[re.compile(r"Last updated \d+")],
        )
    )
    session.flush()

    saved_page = session.execute(text("SELECT url, links, ignore_rules FROM critical_pages")).one()

    assert saved_page.url == "https://example.com/fees"
    assert json.loads(saved_page.links) == ["https://example.com/apply"]
    assert json.loads(saved_page.ignore_rules) == [r"Last updated \d+"]


def test_a_website_not_in_the_database_has_no_internal_links(session: Session) -> None:
    """Tests a website that has not been saved counts no internal links, rather than failing."""
    assert DBWebsite(url="https://example.com").internal_link_count == 0


def test_a_website_only_counts_its_own_internal_links(session: Session) -> None:
    """Tests a website's internal link count leaves out the links of other websites."""
    website, other_website = DBWebsite(url="https://example.com"), DBWebsite(url="https://other.example.com")
    session.add_all([website, other_website])
    session.flush()
    session.add_all(
        [
            DBInternalLink(url="https://example.com/a", website_id=website.id),
            DBInternalLink(url="https://example.com/b", website_id=website.id),
            DBInternalLink(url="https://other.example.com/a", website_id=other_website.id),
        ]
    )
    session.flush()

    assert (website.internal_link_count, other_website.internal_link_count) == (2, 1)


# Valid random UUIDs that SQLite would save as numbers: digits and one "e" (infinity), and only digits
NUMBER_LIKE_IDS: list[uuid.UUID] = [
    uuid.UUID("05527661-3766-4137-8880-39443795e762"),
    uuid.UUID("99999999-9999-4999-8999-9999999e9999"),
    uuid.UUID("12345678-1234-4123-8123-123456789012"),
]


def test_new_ids_are_never_written_like_numbers(mocker: MockerFixture) -> None:
    """Tests a random UUID written only in digits (or digits and one "e") is drawn again, so SQLite never saves an ID
    as a number."""
    usable_id = uuid.UUID("4f1c0a3b-2d5e-4a6f-8b7c-9d0e1f2a3b4c")
    mocker.patch("app.db.schema.uuid.uuid4", side_effect=[*NUMBER_LIKE_IDS, usable_id])

    assert new_id() == usable_id


def test_rows_whose_random_ids_look_like_numbers_are_saved_and_read_back(
    session: Session, mocker: MockerFixture
) -> None:
    """Tests rows still save and read back when the random IDs drawn first look like numbers. SQLite would have saved
    those IDs as numbers, so a second one clashed with the first (failing a scan's save) and reading one back failed
    (breaking the dashboard)."""
    real_uuid4 = uuid.uuid4
    drawn: list[uuid.UUID] = [*NUMBER_LIKE_IDS, *NUMBER_LIKE_IDS]
    mocker.patch("app.db.schema.uuid.uuid4", side_effect=lambda: drawn.pop() if drawn else real_uuid4())

    session.add_all([DBWebsite(url="https://example.com"), DBWebsite(url="https://other.example.com")])
    session.flush()
    [website, other_website] = session.scalars(select(DBWebsite).order_by(DBWebsite.url)).all()
    repository.bulk_insert(
        session,
        DBInternalLink,
        [{"url": f"https://example.com/{page}", "website_id": website.id} for page in range(3)],
    )

    assert drawn == []  # Every number-like ID was drawn, and none was used
    assert [website.url, other_website.url] == ["https://example.com", "https://other.example.com"]
    assert website.internal_link_count == 3
    stored_types = session.execute(
        text("SELECT DISTINCT typeof(id) FROM websites UNION SELECT typeof(id) FROM internal_links")
    )
    assert {row[0] for row in stored_types} == {"text"}
