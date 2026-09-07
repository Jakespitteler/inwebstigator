import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.utils.field_types import URLString


class CriticalPageBase(BaseModel):
    url: URLString
    links: list[URLString] = Field(default_factory=list[URLString])
    documents: list[URLString] = Field(default_factory=list[URLString])
    text_body: str = Field(default="")


class CriticalPageCreate(CriticalPageBase):
    website_id: uuid.UUID | None = None


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    links: list[URLString] | None
    documents: list[URLString] | None
    text_body: str | None
    website_id: uuid.UUID | None


class CriticalPageUpdate(BaseModel):
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None


class CriticalPageState(BaseModel):
    id: uuid.UUID
    url: URLString
    updates: CriticalPageUpdate = CriticalPageUpdate()

    links_added: list[str] | None = None
    links_removed: list[str] | None = None
    documents_added: list[str] | None = None
    documents_removed: list[str] | None = None
    text_added: list[str] | None = None
    text_removed: list[str] | None = None
