import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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
    """The values saved for a critical page: its settings, and the copy of the page the scanner saves."""

    url: HttpUrl | None = None
    links: list[HttpUrl] | None = None
    documents: list[HttpUrl] | None = None
    text_body: str | None = None
    ignore_rules: list[re.Pattern[str]] | None = None

    consecutive_failures: int | None = None
    last_failure_reason: str | None = None


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
