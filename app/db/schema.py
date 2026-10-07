import re
import uuid
from collections.abc import Sequence
from datetime import datetime

from pydantic import HttpUrl
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
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


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(), primary_key=True, default=uuid.uuid4)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
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
    last_email_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    days_between_health_checks: Mapped[float] = mapped_column(
        Float, nullable=False, default=config.scheduler_default_days_between_health_checks
    )


class DBInternalLink(Base):
    __tablename__ = "internal_links"

    url: Mapped[str] = mapped_column(URLText, unique=True, nullable=False, index=True)
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="internal_links")


class DBCriticalPage(Base):
    __tablename__ = "critical_pages"
    __table_args__ = (UniqueConstraint("url", "website_id", name="uq_critical_page_url_website"),)

    url: Mapped[str] = mapped_column(URLText, nullable=False, index=True)
    links: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    documents: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    text_body: Mapped[str] = mapped_column(String, nullable=True)
    ignore_rules: Mapped[list[str]] = mapped_column(TextList, nullable=True)

    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="critical_pages")

    recent_links_added: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    recent_links_removed: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    recent_documents_added: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    recent_documents_removed: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    recent_text_added: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_text_removed: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_text_changed: Mapped[list[str]] = mapped_column(JSON, nullable=True)

    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_reason: Mapped[str | None] = mapped_column(String, nullable=True)  # A DeactivationReason
    failed_attempts_at_min_speed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    internal_links: Mapped[list[DBInternalLink]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    critical_pages: Mapped[list[DBCriticalPage]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    recipients: Mapped[list[DBRecipient]] = relationship(secondary=website_recipient_association)
    recent_added_internal_links: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    recent_removed_internal_links: Mapped[list[str]] = mapped_column(TextList, nullable=True)
    internal_links_last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
