"""What a scan of a website finds: each critical page as it is now and what changed on it, and the pages found on the
website.

They are kept apart from the update models, which only hold what is saved, so what a scan found is never mixed up
with the settings the dashboard changes. A result is saved with `WebsiteService.save_scan_result`, and what changed
is listed in the website's scan history by `changes_found_by`.
"""

import uuid

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.core.config import config
from app.models.content_block_models import ChangedBlock, ContentBlock
from app.models.critical_page_models import CriticalPageUpdate

ALERT_AFTER_FAILURES: int = config.critical_page_alert_after_failures


class CriticalPageScanResult(BaseModel):
    """What one scan found on one critical page: the page as it is now, and what changed since the last scan.

    Attributes:
        url: The critical page.
        links: The links in the page's main content now, if they need saving.
        documents: The documents in the page's main content now, if they need saving.
        text_body: The page's HTML now, if it needs saving.
        consecutive_failures: How many checks of the page have failed in a row, if it changed.
        last_failure_reason: Why the page's last check failed, if it changed.
        recent_links_added: Links that are new on the page.
        recent_links_removed: Links that are no longer on the page.
        recent_documents_added: Documents that are new on the page.
        recent_documents_removed: Documents that are no longer on the page.
        recent_text_added: Blocks of text that are new on the page.
        recent_text_removed: Blocks of text that are no longer on the page.
        recent_text_changed: Blocks of text that were edited or moved to a different section.
    """

    # Values set after the result is made (e.g. by the change detection) are checked too, so a bad link cannot be saved
    model_config = ConfigDict(validate_assignment=True)

    url: HttpUrl
    links: list[HttpUrl] | None = None
    documents: list[HttpUrl] | None = None
    text_body: str | None = None
    consecutive_failures: int | None = None
    last_failure_reason: str | None = None

    recent_links_added: list[HttpUrl] | None = None
    recent_links_removed: list[HttpUrl] | None = None
    recent_documents_added: list[HttpUrl] | None = None
    recent_documents_removed: list[HttpUrl] | None = None
    recent_text_added: list[ContentBlock] | None = None
    recent_text_removed: list[ContentBlock] | None = None
    recent_text_changed: list[ChangedBlock] | None = None

    @property
    def has_just_reached_failure_limit(self) -> bool:
        """Whether this scan is the one where the page reached the failure limit, which is reported once."""
        return self.consecutive_failures == ALERT_AFTER_FAILURES

    @property
    def has_changes(self) -> bool:
        """Whether this result has anything to report: a change to the page, or the page becoming unreachable."""
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

    def as_critical_page_update(self) -> CriticalPageUpdate:
        """Picks out the page as it is now, to save over the page's saved copy. What changed goes in the scan's
        history instead.

        Returns:
            The update, with only the values this scan set.
        """
        return CriticalPageUpdate(**self.model_dump(exclude_unset=True, include=set(CriticalPageUpdate.model_fields)))


class WebsiteScanResult(BaseModel):
    """What one scan of a website found: each critical page's result, and the pages found on the website.

    A website's first scan (or one that saves a new baseline) lists every page it found, and later scans list the
    pages added and removed since the last scan.

    Attributes:
        critical_page_updates: The result of each critical page that changed, saved a baseline, failed or recovered,
            by page ID.
        initial_internal_links: Every page found on the website, when saving a baseline.
        recent_added_internal_links: Pages found on the website that were not there at the last scan.
        recent_removed_internal_links: Pages from the last scan that are no longer on the website.
    """

    # Values set after the result is made (e.g. by the change detection) are checked too, so a bad link cannot be saved
    model_config = ConfigDict(validate_assignment=True)

    critical_page_updates: dict[uuid.UUID, CriticalPageScanResult] | None = None
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
