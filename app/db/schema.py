from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.core import Base


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
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    documents: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    text_body: Mapped[str] = mapped_column(String, nullable=True)

    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), nullable=False)
    website: Mapped["DBWebsite"] = relationship(back_populates="critical_pages")

    previous_run_added_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_added_documents: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_added_text: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_removed_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_removed_documents: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_removed_text: Mapped[list[str]] = mapped_column(JSON, nullable=True)


class DBWebsite(Base):
    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("url", "user_id", name="uq_website_url_user"),)

    url: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user: Mapped[DBUser] = relationship(back_populates="websites")
    internal_links: Mapped[list[DBInternalLink]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )  # having passive_delete=True here will speed up deletion but won't execute changes properly
    critical_pages: Mapped[list[DBCriticalPage]] = relationship(
        back_populates="website",
        cascade="all, delete-orphan",
    )
    previous_run_added_internal_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
    previous_run_removed_internal_links: Mapped[list[str]] = mapped_column(JSON, nullable=True)
