import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import HttpUrl
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Select,
    String,
    Table,
    TypeDecorator,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy import UUID as PG_UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, object_session, relationship

from app.core.config import config


def _as_text(value: HttpUrl | re.Pattern[str] | str) -> str:
    """Turns a value pydantic has validated back into the text it was written as.

    Args:
        value: A URL, a regular expression or some text.

    Returns:
        The value as text, e.g. a regular expression's pattern rather than "re.compile(...)".
    """
    return value.pattern if isinstance(value, re.Pattern) else str(value)


class URLText(TypeDecorator[str]):
    """A text column for a URL, so a pydantic HttpUrl (which is not a str) can be saved as it is."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: HttpUrl | str | None, dialect: Dialect) -> str | None:
        """Turns the URL into text before it is saved.

        Args:
            value: The URL to save.
            dialect: The database being saved to.

        Returns:
            The URL as text, or None if there is no URL.
        """
        return None if value is None else str(value)


class TextList(TypeDecorator[list[str]]):
    """A JSON list column whose items are saved as text, e.g. pydantic URLs or regular expressions."""

    impl = JSON
    cache_ok = True

    def process_bind_param(
        self, value: Sequence[HttpUrl | re.Pattern[str] | str] | None, dialect: Dialect
    ) -> list[str] | None:
        """Turns each item into text before the list is saved.

        Args:
            value: The items to save.
            dialect: The database being saved to.

        Returns:
            The items as text, or None if there is no list.
        """
        return None if value is None else [_as_text(item) for item in value]


class UTCDateTime(TypeDecorator[datetime]):
    """A date and time column that is saved in UTC and read back as a UTC time, with its time zone.

    SQLite has no type for a time with a time zone, so `DateTime(timezone=True)` drops the time zone when saving
    and reads back a time without one. This column turns every time into UTC before saving it, and marks every time
    it reads as UTC, so the app only ever works with UTC times that know their time zone.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Turns a time into UTC, without its time zone, before it is saved.

        Args:
            value: The time to save, which must have a time zone, e.g. `datetime.now(UTC)`.
            dialect: The database being saved to.

        Returns:
            The time in UTC, or None if there is no time.

        Raises:
            ValueError: If the time has no time zone, as it is not known which time zone it is in.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{value} has no time zone, so it cannot be saved as UTC. Use e.g. datetime.now(UTC).")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Marks a time read from the database as UTC, which every saved time is.

        Args:
            value: The time as read, without a time zone.
            dialect: The database being read from.

        Returns:
            The time in UTC, or None if there is no time.
        """
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(), primary_key=True, default=uuid.uuid4)

    # CURRENT_TIMESTAMP is SQLite's current time in UTC
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


website_recipient_association = Table(
    "website_recipients",
    Base.metadata,
    Column("website_id", PG_UUID(as_uuid=True), ForeignKey("websites.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "recipient_id",
        PG_UUID(as_uuid=True),
        ForeignKey("recipients.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class DBRecipient(Base):
    __tablename__ = "recipients"

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    last_email_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    days_between_health_checks: Mapped[float] = mapped_column(
        Float, nullable=False, default=config.scheduler_default_days_between_health_checks
    )


class DBInternalLink(Base):
    __tablename__ = "internal_links"
    # A page is unique within its website, but can belong to two websites (e.g. example.com and example.com/research)
    __table_args__ = (Index("uq_internal_link_url_website", "url", "website_id", unique=True),)

    url: Mapped[str] = mapped_column(URLText, nullable=False, index=True)
    # Indexed on its own, as the unique index starts with the URL so cannot find a website's links (e.g. to count them)
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    website: Mapped["DBWebsite"] = relationship(back_populates="internal_links")


class DBCriticalPage(Base):
    __tablename__ = "critical_pages"
    __table_args__ = (UniqueConstraint("url", "website_id", name="uq_critical_page_url_website"),)

    url: Mapped[str] = mapped_column(URLText, nullable=False, index=True)
    links: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    documents: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    text_body: Mapped[str] = mapped_column(String, nullable=True)
    ignore_rules: Mapped[list[str]] = mapped_column(TextList, nullable=True)

    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    website: Mapped["DBWebsite"] = relationship(back_populates="critical_pages")

    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class DBChange(Base):
    """One thing a scan found, e.g. a new link on a critical page or an edited paragraph.

    Which of the optional columns are set depends on the kind of change (see `ChangeCreate`).
    """

    __tablename__ = "changes"

    scan_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scan_run: Mapped["DBScanRun"] = relationship(back_populates="changes")
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # Keeps the changes in the order they were found
    kind: Mapped[str] = mapped_column(String, nullable=False)  # A ChangeKind

    page_url: Mapped[str | None] = mapped_column(URLText, nullable=True)
    url: Mapped[str | None] = mapped_column(URLText, nullable=True)
    old_block: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_block: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DBScanRun(Base):
    """One scan of one website: when it ran, how it went, what it found and whether its report has been emailed."""

    __tablename__ = "scan_runs"

    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    website: Mapped["DBWebsite"] = relationship(back_populates="scan_runs")
    scanned_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # A ScanStatus
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    changes: Mapped[list[DBChange]] = relationship(
        back_populates="scan_run",
        cascade="all, delete-orphan",
        order_by=DBChange.position,
    )


class DBWebsite(Base):
    __tablename__ = "websites"

    url: Mapped[str] = mapped_column(URLText, nullable=False, index=True)

    recommended_delay: Mapped[float] = mapped_column(Float, nullable=False, default=config.web_crawler_default_delay)
    recommended_concurrent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=config.web_crawler_default_concurrent,
    )
    days_between_scans: Mapped[float] = mapped_column(
        Float, nullable=False, default=config.scheduler_default_days_between_scans
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_reason: Mapped[str | None] = mapped_column(String, nullable=True)  # A DeactivationReason
    card_title: Mapped[str | None] = mapped_column(String, nullable=True)  # Saved by each scan, see website_titles.py
    failed_attempts_at_min_speed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_cooldown_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    internal_links: Mapped[list[DBInternalLink]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    critical_pages: Mapped[list[DBCriticalPage]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    recipients: Mapped[list[DBRecipient]] = relationship(secondary=website_recipient_association)
    scan_runs: Mapped[list[DBScanRun]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )

    @property
    def internal_link_count(self) -> int:
        """The number of internal links saved for the website.

        Counted by the database instead of loading the links, as a large website has tens of thousands
        and loading them all every time the website is read slows the whole app down.
        """
        session: Session | None = object_session(self)
        if session is None:  # Not in the database, so it has no saved links
            return 0
        count_links: Select[int] = (
            select(func.count()).select_from(DBInternalLink).where(DBInternalLink.website_id == self.id)
        )
        return session.scalar(count_links) or 0
