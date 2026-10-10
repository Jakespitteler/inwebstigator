import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, HttpUrl, TypeAdapter, model_validator

from app.core.config import config
from app.core.urls import add_missing_scheme, resolve_critical_page_url
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.recipient_models import RecipientRead

DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
MIN_CONCURRENT: int = config.web_crawler_min_concurrent
DEFAULT_DAYS_BETWEEN_SCANS: float = config.scheduler_default_days_between_scans
MINIMUM_DAYS_BETWEEN_SCANS: float = config.scheduler_minimum_days_between_scans


def _add_missing_scheme_to_text(url: Any) -> Any:
    """Adds "https://" to a URL typed as text without it, e.g. "example.com" becomes "https://example.com".

    Anything that is not text (e.g. a URL that is already an HttpUrl) is left for pydantic to check.

    Args:
        url: The URL as it was given.

    Returns:
        The URL with a scheme if it was text, otherwise the URL unchanged.
    """
    return add_missing_scheme(url) if isinstance(url, str) else url


NewHttpUrl = Annotated[HttpUrl, BeforeValidator(_add_missing_scheme_to_text)]
URL_LIST_ADAPTER: TypeAdapter[list[HttpUrl]] = TypeAdapter(list[HttpUrl])


class DeactivationReason(StrEnum):
    """Why the app deactivated a website itself, so the dashboard can tell the user.

    A website switched off on the dashboard has no reason saved.
    """

    TOO_LARGE = "too_large"
    RATE_LIMITED = "rate_limited"


class WebsiteCreate(BaseModel):
    url: NewHttpUrl
    critical_pages: list[str] = Field(default_factory=list[str], examples=[[""]])
    recipient_emails: list[EmailStr] = Field(default_factory=list[EmailStr], examples=[[""]])
    recommended_delay: float = DEFAULT_DELAY
    # Fewer than one request at a time would leave every scan waiting forever
    recommended_concurrent: int = Field(default=DEFAULT_CONCURRENT, ge=MIN_CONCURRENT)
    days_between_scans: float = Field(default=DEFAULT_DAYS_BETWEEN_SCANS, ge=MINIMUM_DAYS_BETWEEN_SCANS)

    @model_validator(mode="after")
    def resolve_critical_pages(self) -> Self:
        """Turns each critical page into a full URL on the website, e.g. "/news" becomes "https://example.com/news".

        Raises:
            ValueError: If a critical page is on a different website or is not a valid URL.
        """
        resolved_page_urls: list[str] = [
            resolve_critical_page_url(str(self.url), page_url) for page_url in self.critical_pages
        ]
        self.critical_pages = [str(page_url) for page_url in URL_LIST_ADAPTER.validate_python(resolved_page_urls)]
        return self


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: HttpUrl

    recipients: list[RecipientRead]
    recommended_delay: float
    recommended_concurrent: int
    days_between_scans: float

    last_scan_at: datetime | None = None
    active: bool
    deactivated_reason: DeactivationReason | None = None
    failed_attempts_at_min_speed: int
    on_cooldown_until: datetime | None = None
    card_title: str | None = None

    critical_pages: list[CriticalPageRead]
    internal_link_count: int


class WebsiteUpdate(BaseModel):
    # Values set after the update is made (e.g. by the change detection) are checked too, so a bad link cannot be saved
    model_config = ConfigDict(validate_assignment=True)

    url: HttpUrl | None = None

    recommended_delay: float | None = None
    recommended_concurrent: int | None = None
    days_between_scans: float | None = Field(default=None, ge=MINIMUM_DAYS_BETWEEN_SCANS)

    last_scan_at: datetime | None = None
    active: bool | None = None
    deactivated_reason: DeactivationReason | None = None
    failed_attempts_at_min_speed: int | None = None
    on_cooldown_until: datetime | None = None
    card_title: str | None = None

    critical_page_updates: dict[uuid.UUID, CriticalPageUpdate] | None = None

    add_recipient_emails: list[EmailStr] | None = None
    remove_recipient_emails: list[EmailStr] | None = None

    initial_internal_links: list[HttpUrl] | None = None
    recent_added_internal_links: list[HttpUrl] | None = None
    recent_removed_internal_links: list[HttpUrl] | None = None

    @property
    def changed_page_ids(self) -> set[uuid.UUID]:
        """IDs of the critical pages a scan found changes on, excluding pages that only saved a baseline."""
        return {page_id for page_id, page in (self.critical_page_updates or {}).items() if page.has_changes}

    @property
    def has_changes(self) -> bool:
        """Whether a scan found changes worth reporting, as opposed to only saving baselines."""
        return bool(self.changed_page_ids or self.recent_added_internal_links or self.recent_removed_internal_links)


class WebsiteSettingsUpdate(BaseModel):
    """The website settings the dashboard can change.

    The rest of `WebsiteUpdate` is only for the scanner to save what it found, so it is not accepted from requests.
    """

    recommended_delay: float | None = None
    recommended_concurrent: int | None = Field(default=None, ge=MIN_CONCURRENT)
    days_between_scans: float | None = Field(default=None, ge=MINIMUM_DAYS_BETWEEN_SCANS)
    active: bool | None = None

    add_recipient_emails: list[EmailStr] | None = None
    remove_recipient_emails: list[EmailStr] | None = None

    def as_website_update(self) -> WebsiteUpdate:
        """Turns the settings into an update of the website, changing only the settings that were given.

        Returns:
            The update.
        """
        return WebsiteUpdate(**self.model_dump(exclude_unset=True))
