import uuid

from pydantic import BaseModel, ConfigDict, HttpUrl


class InternalLinkCreate(BaseModel):
    url: HttpUrl
    website_id: uuid.UUID


class InternalLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: HttpUrl
    website_id: uuid.UUID


class InternalLinkUpdate(BaseModel):
    url: HttpUrl | None = None
