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
