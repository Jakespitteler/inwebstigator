import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.core.config import config
from app.models.content_block_models import ChangedBlock, ContentBlock

ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures


class CriticalPageCreate(BaseModel):
    url: HttpUrl
    website_id: uuid.UUID | None = Field(default=None, examples=[""])
    ignore_rules: list[re.Pattern[str]] = Field(default_factory=list[re.Pattern[str]])


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    url: HttpUrl
    links: list[HttpUrl] | None = None
    documents: list[HttpUrl] | None = None
    text_body: str | None = None
    ignore_rules: list[str] | None = None

    consecutive_failures: int = 0
    last_failure_reason: str | None = None


class CriticalPageUpdate(BaseModel):
    # Values set after the update is made (e.g. by the change detection) are checked too, so a bad link cannot be saved
    model_config = ConfigDict(validate_assignment=True)

    url: HttpUrl | None = None
    links: list[HttpUrl] | None = None
    documents: list[HttpUrl] | None = None
    text_body: str | None = None
    ignore_rules: list[re.Pattern[str]] | None = None

    recent_links_added: list[HttpUrl] | None = None
    recent_links_removed: list[HttpUrl] | None = None
    recent_documents_added: list[HttpUrl] | None = None
    recent_documents_removed: list[HttpUrl] | None = None
    recent_text_added: list[ContentBlock] | None = None
    recent_text_removed: list[ContentBlock] | None = None
    recent_text_changed: list[ChangedBlock] | None = None

    consecutive_failures: int | None = None
    last_failure_reason: str | None = None

    @property
    def has_just_reached_failure_limit(self) -> bool:
        """Whether this scan is the one where the page reached the failure limit, which is reported once."""
        return self.consecutive_failures == ALERT_AFTER_FAILURES

    @property
    def has_changes(self) -> bool:
        """Whether this update has anything to report: a change to the page, or the page becoming unreachable."""
        return any(
            (
                self.recent_links_added,
                self.recent_links_removed,
                self.recent_documents_added,
                self.recent_documents_removed,
                self.recent_text_added,
                self.recent_text_removed,
                self.recent_text_changed,
                self.has_just_reached_failure_limit,
            )
        )


class CriticalPageSettingsUpdate(BaseModel):
    """The critical page settings that can be changed through the API.

    The rest of `CriticalPageUpdate` is only for the scanner to save what it found (e.g. the page's saved copy), so it
    is not accepted from requests.
    """

    ignore_rules: list[re.Pattern[str]] | None = None

    def as_critical_page_update(self) -> CriticalPageUpdate:
        """Turns the settings into an update of the critical page, changing only the settings that were given.

        Returns:
            The update.
        """
        return CriticalPageUpdate(**self.model_dump(exclude_unset=True))
