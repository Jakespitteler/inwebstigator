import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.utils.field_types import URLString


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
    url: URLString
    links: list[URLString]
    documents: list[URLString]
    text_body: str
    website_id: uuid.UUID


class CriticalPageUpdate(BaseModel):
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None


class ContentBlock(BaseModel):
    parent_heading: str
    block_type: str
    text: str


class ChangedBlock(BaseModel):
    """Represents a block that was edited rather than completely replaced."""

    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float


class PageContent(BaseModel):
    headings: list[str] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    links: list[str] = Field(default_factory=list)
    last_updated: str | None = Field(default=None)


class CriticalPageState(BaseModel):
    id: uuid.UUID
    url: URLString
    updates: CriticalPageUpdate = CriticalPageUpdate()

    links_added: list[str] | None = None
    links_removed: list[str] | None = None
    documents_added: list[str] | None = None
    documents_removed: list[str] | None = None
    text_added: list[ContentBlock] | None = None
    text_removed: list[ContentBlock] | None = None
    text_changed: list[ChangedBlock] | None = None
