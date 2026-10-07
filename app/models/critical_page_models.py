import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.backend.diff_checker.models import ChangedBlock, ContentBlock
from app.core.config import config
from app.models.field_types import RegexString, URLString


class CriticalPageCreate(BaseModel):
    url: URLString
    website_id: uuid.UUID | None = Field(default=None, examples=[""])
    ignore_rules: list[RegexString] = Field(default_factory=list[RegexString])


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    url: URLString
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None
    ignore_rules: list[str] | None = None

    recent_links_added: list[URLString] | None = None
    recent_links_removed: list[URLString] | None = None
    recent_documents_added: list[URLString] | None = None
    recent_documents_removed: list[URLString] | None = None
    recent_text_added: list[ContentBlock] | None = None
    recent_text_removed: list[ContentBlock] | None = None
    recent_text_changed: list[ChangedBlock] | None = None
    last_changed_at: datetime | None = None

    consecutive_failures: int = 0
    last_failure_reason: str | None = None


class CriticalPageUpdate(BaseModel):
    url: URLString | None = None
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None
    ignore_rules: list[RegexString] | None = None

    recent_links_added: list[URLString] | None = None
    recent_links_removed: list[URLString] | None = None
    recent_documents_added: list[URLString] | None = None
    recent_documents_removed: list[URLString] | None = None
    recent_text_added: list[ContentBlock] | None = None
    recent_text_removed: list[ContentBlock] | None = None
    recent_text_changed: list[ChangedBlock] | None = None
    last_changed_at: datetime | None = None

    consecutive_failures: int | None = None
    last_failure_reason: str | None = None

    @property
    def has_just_reached_failure_limit(self) -> bool:
        """Whether this scan is the one where the page reached the failure limit, which is reported once."""
        return self.consecutive_failures == config.critical_page_alert_after_failures

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
