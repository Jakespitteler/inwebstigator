import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.critical_page_models import CriticalPageBase, CriticalPageRead, CriticalPageUpdate
from app.db.models.internal_link_models import InternalLinkRead
from app.db.utils.field_types import URLString


class WebsiteCreate(BaseModel):
    url: URLString
    user_id: uuid.UUID
    critical_pages: list[CriticalPageBase] = Field(default_factory=list[CriticalPageBase])
    internal_links: list[URLString] = Field(default_factory=list[URLString])


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    url: URLString
    critical_pages: list[CriticalPageRead] | None = None
    internal_links: list[InternalLinkRead] | None = None
    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None


class WebsiteUpdate(BaseModel):
    url: URLString | None = None
    critical_page_updates: dict[str, CriticalPageUpdate] | None = None
    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None
