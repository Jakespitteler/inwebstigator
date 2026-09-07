import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.critical_page_models import CriticalPageBase, CriticalPageRead, CriticalPageState
from app.db.models.internal_link_models import InternalLinkRead
from app.db.utils.field_types import URLString


class WebsiteCreate(BaseModel):
    url: URLString
    recommended_delay: float = 1
    recommended_concurrent: int = 5
    critical_pages: list[CriticalPageBase] = Field(default_factory=list[CriticalPageBase])
    internal_links: list[URLString] = Field(default_factory=list[URLString])


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    recommended_delay: float
    recommended_concurrent: int
    next_scan_at: datetime | None = None
    critical_pages: list[CriticalPageRead]
    internal_links: list[InternalLinkRead]


class WebsiteUpdate(BaseModel):
    url: URLString | None = None
    recommended_delay: float | None = None
    recommended_concurrent: int | None = None
    next_scan_at: datetime | None = None


class WebsiteState(BaseModel):
    id: uuid.UUID
    url: URLString

    critical_page_states: list[CriticalPageState] = Field(default_factory=list[CriticalPageState])
    added_internal_links: list[str] | None = None
    removed_internal_links: list[str] | None = None
