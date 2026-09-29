import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import config
from app.db.utils.field_types import EmailString, URLString
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead

DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
DEFAULT_DAYS_BETWEEN_SCANS: float = config.scheduler_default_days_between_scans


class WebsiteCreate(BaseModel):
    url: URLString
    critical_pages: list[URLString] = Field(default_factory=list[URLString], examples=[[""]])
    recipient_emails: list[EmailString] = Field(default_factory=list[EmailString], examples=[[""]])
    recommended_delay: float = DEFAULT_DELAY
    recommended_concurrent: int = DEFAULT_CONCURRENT
    days_between_scans: float = DEFAULT_DAYS_BETWEEN_SCANS


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString

    recipients: list[RecipientRead]
    recommended_delay: float
    recommended_concurrent: int
    days_between_scans: float

    last_scan_at: datetime | None = None
    active: bool
    failed_attempts_at_min_speed: int
    on_cooldown_until: datetime | None = None

    critical_pages: list[CriticalPageRead]
    internal_links: list[InternalLinkRead]

    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None


class WebsiteUpdate(BaseModel):
    url: URLString | None = None

    recommended_delay: float | None = None
    recommended_concurrent: int | None = None
    days_between_scans: float | None = None

    last_scan_at: datetime | None = None
    active: bool | None = None
    failed_attempts_at_min_speed: int | None = None
    on_cooldown_until: datetime | None = None

    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] | None = None

    add_recipient_emails: list[EmailString] | None = None
    remove_recipient_emails: list[EmailString] | None = None

    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None
