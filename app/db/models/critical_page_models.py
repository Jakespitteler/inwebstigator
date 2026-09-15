import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.backend.diff_checker.compare_content import ChangedBlock, ContentBlock
from app.db.utils.field_types import URLString


class CriticalPageCreate(BaseModel):
    url: URLString
    website_id: uuid.UUID | None = Field(default=None, examples=[""])


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    url: URLString
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


class CriticalPageUpdate(BaseModel):
    url: URLString | None = None
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
