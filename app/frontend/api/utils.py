from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from markupsafe import Markup, escape

from app.backend.diff_checker.word_diff import DiffWord, WordChange, WordDiff, diff_words
from app.models.field_types import URLString


def format_timestamp(moment: datetime) -> str:
    """Displays a date and time the same way across the dashboard, e.g. "05 Oct 2026, 09:00"."""
    # %H rather than %-I, which is not supported on Windows where the desktop app runs
    return moment.strftime("%d %b %Y, %H:%M")


def scan_time(value: datetime | None) -> str:
    """Display when a website was last scanned, or that it hasn't been yet."""
    if value is None:
        return "Not scanned yet"
    return format_timestamp(value)


@dataclass
class TextChangeRecord:
    old_section: str | None
    new_section: str | None
    old: str
    new: str
    old_html: Markup
    new_html: Markup
    similarity: float


@dataclass
class ContentBlockRecord:
    section: str | None
    text: str
    block_type: str


@dataclass
class DailyRecord:
    url: URLString
    website_url: URLString
    changed: list[TextChangeRecord] = field(default_factory=list[TextChangeRecord])
    added: list[ContentBlockRecord] = field(default_factory=list[ContentBlockRecord])
    removed: list[ContentBlockRecord] = field(default_factory=list[ContentBlockRecord])
    links_added: list[str] = field(default_factory=list[str])
    links_removed: list[str] = field(default_factory=list[str])
    documents_added: list[str] = field(default_factory=list[str])
    documents_removed: list[str] = field(default_factory=list[str])
    changed_at: datetime | None = None


@dataclass
class WebsiteDailyRecord:
    """A website's changes from its latest scan: each critical page that changed and its new or removed pages.

    Attributes:
        website_url (URLString): The URL of the website the critical pages belong to.
        pages (list[DailyRecord]): The changes found on each of the website's critical pages.
        internal_links_added (list[str]): Pages found on the website that were not there in the previous scan.
        internal_links_removed (list[str]): Pages from the previous scan that are no longer on the website.
        internal_links_changed_at (datetime | None): When the added and removed internal links were found.
    """

    website_url: URLString
    pages: list[DailyRecord]
    internal_links_added: list[str] = field(default_factory=list[str])
    internal_links_removed: list[str] = field(default_factory=list[str])
    internal_links_changed_at: datetime | None = None

    @property
    def changed_at(self) -> datetime | None:
        """When the most recent of the website's changes was found, or None if no time was recorded for any."""
        # An old time is kept after its internal link changes are cleared, so it only counts while they are shown
        has_internal_link_changes: bool = bool(self.internal_links_added or self.internal_links_removed)
        internal_links_time: datetime | None = self.internal_links_changed_at if has_internal_link_changes else None
        found_times: list[datetime] = [
            found_time
            for found_time in (internal_links_time, *(page.changed_at for page in self.pages))
            if found_time is not None
        ]
        return max(found_times, default=None)


class ChangeRecord(Protocol):
    """Anything on the updates page that knows when its changes were found."""

    @property
    def changed_at(self) -> datetime | None: ...


def newest_first[RecordT: ChangeRecord](records: Iterable[RecordT]) -> list[RecordT]:
    """Orders change records so the most recently found changes come first.

    Changes found before the app recorded when changes were found have no time, so they go last.

    Args:
        records (Iterable[RecordT]): The website or critical page records to order.

    Returns:
        list[RecordT]: The records, most recent change first.
    """
    return sorted(records, key=lambda record: record.changed_at or datetime.min, reverse=True)


def _word_html(word: DiffWord) -> Markup:
    """Shows one word of an edited block, highlighted if it was added or removed.

    Args:
        word: The word and how it changed.

    Returns:
        The escaped word, wrapped in a highlight span if it changed.
    """
    if word.change is WordChange.SAME:
        return escape(word.text)
    return Markup(f'<span class="{word.change}-word">{escape(word.text)}</span>')


def build_word_diff(old_text: str, new_text: str) -> tuple[Markup, Markup]:
    """Shows the old and new text of an edited block with the removed and added words highlighted.

    Args:
        old_text: The block's text before the edit.
        new_text: The block's text after the edit.

    Returns:
        The old text and the new text as safe HTML.
    """
    word_diff: WordDiff = diff_words(old_text, new_text)
    return (
        Markup(" ").join(_word_html(word) for word in word_diff.old_words),
        Markup(" ").join(_word_html(word) for word in word_diff.new_words),
    )
