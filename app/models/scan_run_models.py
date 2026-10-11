import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.content_block_models import ChangedBlock, ContentBlock


class ScanStatus(StrEnum):
    """How a website's scan went, which decides the title and colours of its report."""

    SUCCESS = "success"
    TRAFFIC_ERROR = "traffic_error"
    CONNECTION_ERROR = "connection_error"
    SCAN_ERROR = "scan_error"
    SKIPPED_DEACTIVATED = "skipped_deactivated"
    SKIPPED_COOLDOWN = "skipped_cooldown"
    TOO_LARGE = "too_large"
    PAGES_MISSING = "pages_missing"


class ChangeKind(StrEnum):
    """What kind of change a scan found."""

    INTERNAL_LINK_ADDED = "internal_link_added"
    INTERNAL_LINK_REMOVED = "internal_link_removed"
    LINK_ADDED = "link_added"
    LINK_REMOVED = "link_removed"
    DOCUMENT_ADDED = "document_added"
    DOCUMENT_REMOVED = "document_removed"
    TEXT_ADDED = "text_added"
    TEXT_REMOVED = "text_removed"
    TEXT_CHANGED = "text_changed"
    PAGE_UNREACHABLE = "page_unreachable"


class ChangeCreate(BaseModel):
    """One thing a scan found, e.g. a new link on a critical page or an edited paragraph.

    Attributes:
        kind: What kind of change it is, which decides which of the other fields are set.
        page_url: The critical page it was found on, or None for a change to the website's internal links.
        url: The link, document or internal page that was added or removed.
        old_block: The text before it was edited or removed.
        new_block: The text after it was edited or added.
        similarity: How alike the old and new text are, from 0.0 to 1.0, for edited text.
        failure_reason: Why the critical page could not be checked, for an unreachable page.
        failure_count: How many checks of the critical page have failed in a row, for an unreachable page.
    """

    kind: ChangeKind
    page_url: HttpUrl | None = None
    url: HttpUrl | None = None
    old_block: ContentBlock | None = None
    new_block: ContentBlock | None = None
    similarity: float | None = None
    failure_reason: str | None = None
    failure_count: int | None = None


class ChangeRead(ChangeCreate):
    """A change as saved in its scan's history."""

    model_config = ConfigDict(from_attributes=True)


class PageChanges(BaseModel):
    """Everything one scan found on one critical page.

    Attributes:
        url: The critical page.
        links_added: Links that are new on the page.
        links_removed: Links that are no longer on the page.
        documents_added: Documents (e.g. PDFs) that are new on the page.
        documents_removed: Documents that are no longer on the page.
        text_added: Blocks of text that are new on the page.
        text_removed: Blocks of text that are no longer on the page.
        text_changed: Blocks of text that were edited or moved to a different section.
        failure_reason: Why the page could not be checked, or None if it was checked.
        failure_count: How many checks of the page have failed in a row.
    """

    url: HttpUrl
    links_added: list[HttpUrl] = Field(default_factory=list[HttpUrl])
    links_removed: list[HttpUrl] = Field(default_factory=list[HttpUrl])
    documents_added: list[HttpUrl] = Field(default_factory=list[HttpUrl])
    documents_removed: list[HttpUrl] = Field(default_factory=list[HttpUrl])
    text_added: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    text_removed: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    text_changed: list[ChangedBlock] = Field(default_factory=list[ChangedBlock])
    failure_reason: str | None = None
    failure_count: int = 0

    @property
    def is_unreachable(self) -> bool:
        """Whether the page could not be checked, so it is reported as unreachable rather than as changed."""
        return self.failure_reason is not None


def _urls_of_kind(changes: Iterable[ChangeRead], kind: ChangeKind) -> list[HttpUrl]:
    """Lists the added or removed URLs of one kind of change.

    Args:
        changes: The changes to look through.
        kind: The kind of change to keep, e.g. links added.

    Returns:
        The URLs, in the order they were found.
    """
    return [change.url for change in changes if change.kind is kind and change.url is not None]


def _blocks_of_kind(changes: Iterable[ChangeRead], kind: ChangeKind) -> list[ContentBlock]:
    """Lists the blocks of text that were added or removed.

    Args:
        changes: The changes to look through.
        kind: Either text added, which keeps the new blocks, or text removed, which keeps the old blocks.

    Returns:
        The blocks, in page order.
    """
    blocks: list[ContentBlock | None] = [
        change.new_block if kind is ChangeKind.TEXT_ADDED else change.old_block
        for change in changes
        if change.kind is kind
    ]
    return [block for block in blocks if block is not None]


def _edited_blocks(changes: Iterable[ChangeRead]) -> list[ChangedBlock]:
    """Lists the blocks of text that were edited or moved, with their text before and after.

    Args:
        changes: The changes to look through.

    Returns:
        The edited blocks, in the order they were found.
    """
    return [
        ChangedBlock(old_block=change.old_block, new_block=change.new_block, similarity=change.similarity or 0.0)
        for change in changes
        if change.kind is ChangeKind.TEXT_CHANGED and change.old_block and change.new_block
    ]


def group_page_changes(page_url: HttpUrl, changes: Sequence[ChangeRead]) -> PageChanges:
    """Gathers what a scan found on one critical page, sorted by kind of change.

    Args:
        page_url: The critical page.
        changes: The changes found on the page.

    Returns:
        The page's changes.
    """
    failure: ChangeRead | None = next(
        (change for change in changes if change.kind is ChangeKind.PAGE_UNREACHABLE), None
    )
    return PageChanges(
        url=page_url,
        links_added=_urls_of_kind(changes, ChangeKind.LINK_ADDED),
        links_removed=_urls_of_kind(changes, ChangeKind.LINK_REMOVED),
        documents_added=_urls_of_kind(changes, ChangeKind.DOCUMENT_ADDED),
        documents_removed=_urls_of_kind(changes, ChangeKind.DOCUMENT_REMOVED),
        text_added=_blocks_of_kind(changes, ChangeKind.TEXT_ADDED),
        text_removed=_blocks_of_kind(changes, ChangeKind.TEXT_REMOVED),
        text_changed=_edited_blocks(changes),
        failure_reason=failure.failure_reason if failure else None,
        failure_count=(failure.failure_count or 0) if failure else 0,
    )


class ScanRunCreate(BaseModel):
    """A scan of a website to add to its history.

    Attributes:
        website_id: The website that was scanned.
        scanned_at: When the scan finished.
        status: How the scan went.
        message: What the app did about a scan that did not go normally, e.g. putting the website on cooldown.
        changes: What the scan found, in the order it was found.
    """

    website_id: uuid.UUID
    scanned_at: datetime
    status: ScanStatus = ScanStatus.SUCCESS
    message: str | None = None
    changes: list[ChangeCreate] = Field(default_factory=list[ChangeCreate])


class ScanRunRead(BaseModel):
    """One scan from a website's history.

    Attributes:
        id: The scan's ID.
        website_id: The website that was scanned.
        scanned_at: When the scan finished.
        status: How the scan went.
        message: What the app did about a scan that did not go normally, e.g. putting the website on cooldown.
        notified_at: When the scan's report was emailed, or None if it has not been (yet).
        changes: What the scan found, in the order it was found.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    website_id: uuid.UUID
    scanned_at: datetime
    status: ScanStatus
    message: str | None = None
    notified_at: datetime | None = None
    changes: list[ChangeRead] = Field(default_factory=list[ChangeRead])

    @property
    def has_report(self) -> bool:
        """Whether the scan has anything to tell the website's recipients: a change, or a problem with the scan."""
        return self.status is not ScanStatus.SUCCESS or bool(self.changes)

    @property
    def is_awaiting_email(self) -> bool:
        """Whether the scan has a report that has not been emailed yet, e.g. because the mail server was down."""
        return self.has_report and self.notified_at is None

    @property
    def internal_links_added(self) -> list[HttpUrl]:
        """Pages found on the website that were not there at the scan before."""
        return _urls_of_kind(self.changes, ChangeKind.INTERNAL_LINK_ADDED)

    @property
    def internal_links_removed(self) -> list[HttpUrl]:
        """Pages from the scan before that are no longer on the website."""
        return _urls_of_kind(self.changes, ChangeKind.INTERNAL_LINK_REMOVED)

    @property
    def pages(self) -> list[PageChanges]:
        """What the scan found on each critical page, in the order the pages were checked."""
        page_urls: dict[HttpUrl, None] = dict.fromkeys(
            change.page_url for change in self.changes if change.page_url is not None
        )
        return [
            group_page_changes(page_url, [change for change in self.changes if change.page_url == page_url])
            for page_url in page_urls
        ]
