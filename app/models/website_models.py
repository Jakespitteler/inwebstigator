import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.backend.utils.links import add_missing_scheme, resolve_critical_page_url
from app.core.config import config
from app.db.utils.field_types import URL_CONSTRAINTS, EmailString, URLString
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead

DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
DEFAULT_DAYS_BETWEEN_SCANS: float = config.scheduler_default_days_between_scans
MINIMUM_DAYS_BETWEEN_SCANS: float = config.scheduler_minimum_days_between_scans

URL_LIST_ADAPTER: TypeAdapter[list[str]] = TypeAdapter(list[URLString])


class DeactivationReason(StrEnum):
    """Why the app deactivated a website itself, so the dashboard can tell the user."""

    TOO_LARGE = "too_large"


class WebsiteCreate(BaseModel):
    # "https://" is added before the URL rules are checked, so "example.com" can be entered
    url: Annotated[str, AfterValidator(add_missing_scheme), URL_CONSTRAINTS]
    # Critical pages can also be links relative to the website (e.g. "/news") until they are resolved below
    critical_pages: list[str] = Field(default_factory=list[str], examples=[[""]])
    recipient_emails: list[EmailString] = Field(default_factory=list[EmailString], examples=[[""]])
    recommended_delay: float = DEFAULT_DELAY
    recommended_concurrent: int = DEFAULT_CONCURRENT
    days_between_scans: float = Field(default=DEFAULT_DAYS_BETWEEN_SCANS, ge=MINIMUM_DAYS_BETWEEN_SCANS)

    @model_validator(mode="after")
    def resolve_critical_pages(self) -> Self:
        """Turns each critical page into a full URL on the website, e.g. "/news" becomes "https://example.com/news".

        Raises:
            ValueError: If a critical page is on a different website or is not a valid URL.
        """
        resolved_page_urls: list[str] = [
            resolve_critical_page_url(self.url, page_url) for page_url in self.critical_pages
        ]
        self.critical_pages = URL_LIST_ADAPTER.validate_python(resolved_page_urls)
        return self


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
    deactivated_reason: DeactivationReason | None = None
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
    days_between_scans: float | None = Field(default=None, ge=MINIMUM_DAYS_BETWEEN_SCANS)

    last_scan_at: datetime | None = None
    active: bool | None = None
    deactivated_reason: DeactivationReason | None = None
    failed_attempts_at_min_speed: int | None = None
    on_cooldown_until: datetime | None = None

    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] | None = None

    add_recipient_emails: list[EmailString] | None = None
    remove_recipient_emails: list[EmailString] | None = None

    initial_internal_links: list[URLString] | None = None
    recent_added_internal_links: list[URLString] | None = None
    recent_removed_internal_links: list[URLString] | None = None

    @property
    def changed_page_ids(self) -> set[uuid.UUID]:
        """IDs of the critical pages a scan found changes on, excluding pages that only saved a baseline."""
        return {page_id for page_id, page in (self.critical_page_updates or {}).items() if page.has_changes}

    @property
    def has_changes(self) -> bool:
        """Whether a scan found changes worth reporting, as opposed to only saving baselines."""
        return bool(self.changed_page_ids or self.recent_added_internal_links or self.recent_removed_internal_links)
