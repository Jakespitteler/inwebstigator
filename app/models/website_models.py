import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import config
from app.db.utils.field_types import URLString
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead

DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent


class WebsiteCreate(BaseModel):
    url: URLString
    user_id: uuid.UUID = Field(examples=[""])
    critical_pages: list[URLString] = Field(default_factory=list[URLString], examples=[[""]])
    recommended_delay: float = DEFAULT_DELAY
    recommended_concurrent: int = DEFAULT_CONCURRENT


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    url: URLString
    recommended_delay: float
    recommended_concurrent: int

    critical_pages: list[CriticalPageRead]
    internal_links: list[InternalLinkRead]

    active: bool
    on_cooldown_until: datetime | None = None

    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None


class WebsiteUpdate(BaseModel):
    url: URLString | None = None
    recommended_delay: float | None = None
    recommended_concurrent: int | None = None
    on_cooldown_until: datetime | None = None
    active: bool | None = None
    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] | None = None
    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None
