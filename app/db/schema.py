import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(), primary_key=True, default=uuid.uuid4)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class DBUser(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password: Mapped[str] = mapped_column(String, nullable=False)

    websites: Mapped[list["DBWebsite"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class DBInternalLink(Base):
    __tablename__ = "internal_links"

    url: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="internal_links")


class DBCriticalPage(Base):
    __tablename__ = "critical_pages"
    __table_args__ = (UniqueConstraint("url", "website_id", name="uq_critical_page_url_website"),)

    url: Mapped[str] = mapped_column(String, nullable=False, index=True)
    links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    documents: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    text_body: Mapped[str] = mapped_column(String, nullable=True)

    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="critical_pages")

    recent_links_added: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_links_removed: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_documents_added: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_documents_removed: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_text_added: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_text_removed: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_text_changed: Mapped[list[str]] = mapped_column(JSON, nullable=True)


class DBWebsite(Base):
    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("url", "user_id", name="uq_website_url_user"),)

    url: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user: Mapped[DBUser] = relationship(back_populates="websites")
    internal_links: Mapped[list[DBInternalLink]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    critical_pages: Mapped[list[DBCriticalPage]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    recent_added_internal_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    recent_removed_internal_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
