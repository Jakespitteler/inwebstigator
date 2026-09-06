import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.critical_page_models import CriticalPageBase, CriticalPageRead
from app.db.models.internal_link_models import InternalLinkRead
from app.db.utils.field_types import URLString


class WebsiteCreate(BaseModel):
    url: URLString
    critical_pages: list[CriticalPageBase] = Field(default_factory=list[CriticalPageBase])
    internal_links: list[URLString] = Field(default_factory=list[URLString])


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    critical_pages: list[CriticalPageRead]
    internal_links: list[InternalLinkRead]


class WebsiteUpdate(BaseModel):
    url: URLString | None = None
