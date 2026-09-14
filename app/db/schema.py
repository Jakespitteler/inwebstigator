from sqlalchemy import JSON, ForeignKey, String
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
