import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.db.utils.field_types import URLString


class HTMLBlockType(StrEnum):
    PARAGRAPH = "p"
    HEADING_1 = "h1"
    HEADING_2 = "h2"
    HEADING_3 = "h3"
    HEADING_4 = "h4"
    HEADING_5 = "h5"
    HEADING_6 = "h6"
    CODE = "pre"
    QUOTE = "blockquote"
    UNORDERED_LIST = "ul"
    ORDERED_LIST = "ol"
    DIVISION = "div"


class ContentBlock(BaseModel):
    parent_heading: str | None = None
    block_type: HTMLBlockType
    text: str


class ChangedBlock(BaseModel):
    """Represents a block that was edited rather than completely replaced."""

    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float


class CriticalPageBase(BaseModel):
    url: URLString
    links: list[URLString] = Field(default_factory=list[URLString])
    documents: list[URLString] = Field(default_factory=list[URLString])
    text_body: str = Field(default="")


class CriticalPageCreate(CriticalPageBase):
    website_id: uuid.UUID


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    url: URLString
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None

    recent_links_added: list[URLString] = Field(default_factory=list[URLString])
    recent_links_removed: list[URLString] = Field(default_factory=list[URLString])
    recent_documents_added: list[URLString] = Field(default_factory=list[URLString])
    recent_documents_removed: list[URLString] = Field(default_factory=list[URLString])
    recent_text_added: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    recent_text_removed: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    recent_text_changed: list[ChangedBlock] = Field(default_factory=list[ChangedBlock])


class CriticalPageUpdate(BaseModel):
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None

    recent_links_added: list[URLString] | None = None
    recent_links_removed: list[URLString] | None = None
    recent_documents_added: list[URLString] | None = None
    recent_documents_removed: list[URLString] | None = None
    recent_text_added: list[ContentBlock] | None = None
    recent_text_removed: list[ContentBlock] | None = None
    recent_text_changed: list[ChangedBlock] | None = None


class PageContent(BaseModel):
    headings: list[str] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    links: list[str] = Field(default_factory=list)
    last_updated: str | None = Field(default=None)
