import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.core import Base


class DBInternalLink(Base):
    __tablename__ = "internal_links"

    url: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="internal_links")


class DBCriticalPage(Base):
    __tablename__ = "critical_pages"
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    documents: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    text_body: Mapped[str] = mapped_column(String, nullable=True)

    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="critical_pages")


class DBNotificationState(Base):
    """What the notifier needs remembered between runs, one row per website.

    `last_run_at` is separate from `last_email_at`: a quiet run still counts as
    a run, so a restart doesn't trigger a second check, but it must not reset
    the weekly all-clear window.
    """

    __tablename__ = "notification_states"

    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_action: Mapped[str | None] = mapped_column(String, nullable=True)

    website: Mapped["DBWebsite"] = relationship(back_populates="notification_state")


class DBPendingNotification(Base):
    """A report whose send failed, parked so it can be retried.

    Without this a dead mail server costs a day of changes: the diff finder has
    already moved its snapshot on. Stored as serialised Change dicts, so a retry
    sends what the original run would have. `attempts` and `next_attempt_at`
    drive the backoff.
    """

    __tablename__ = "pending_notifications"

    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changes: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    website: Mapped["DBWebsite"] = relationship(back_populates="pending_notifications")


class DBWebsite(Base):
    __tablename__ = "websites"

    url: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    internal_links: Mapped[list["DBInternalLink"]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )  # having passive_delete=True here will speed up deletion but won't execute changes properly
    critical_pages: Mapped[list["DBCriticalPage"]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    notification_state: Mapped["DBNotificationState | None"] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
        uselist=False,
    )
    pending_notifications: Mapped[list["DBPendingNotification"]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
