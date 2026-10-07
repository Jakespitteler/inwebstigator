import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, SoupStrainer
from markupsafe import Markup, escape

from app.db.utils.field_types import URLString


def website_name(url: str, html: str | None = None) -> str:
    """Format the site name and URL path, preserving names recognised in saved metadata."""
    try:
        parsed_url = urlsplit(url)
        hostname = (parsed_url.hostname or "").rstrip(".")
    except ValueError:
        return "Website"
    if not hostname:
        return "Website"

    try:
        ip_address(hostname)
        return _website_name_with_path(hostname, parsed_url.path)
    except ValueError:
        pass

    labels = hostname.removeprefix("www.").split(".")
    # Common country-code endings, e.g. uwa.edu.au and bbc.co.uk.
    country_categories = {"ac", "asn", "co", "com", "edu", "gov", "id", "mil", "net", "org"}
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in country_categories:
        name = labels[-3]
    else:
        name = labels[-2] if len(labels) > 1 else labels[0]

    if html:
        recognised_name = _website_name_from_html(name, html)
        if recognised_name:
            return _website_name_with_path(recognised_name, parsed_url.path)
    short_name = name.replace("-", " ").replace("_", " ").title()
    return _website_name_with_path(short_name, parsed_url.path)


def _website_name_with_path(name: str, path: str) -> str:
    """Append readable path sections without query parameters or fragments."""
    sections = [
        unquote(section).replace("-", " ").replace("_", " ").strip().title()
        for section in path.split("/")
        if section
    ]
    return " - ".join([name, *(section for section in sections if section)])


def _website_name_from_html(name: str, html: str) -> str | None:
    """Match the domain label to words in site metadata, avoiding article titles."""
    soup = BeautifulSoup(html, "html.parser", parse_only=SoupStrainer(["title", "meta"]))
    candidates = [
        str(meta.get("content") or "")
        for meta in soup.find_all("meta")
        if str(meta.get("property") or meta.get("name") or "").lower() in {"og:site_name", "application-name"}
    ]
    candidates.extend(title.get_text(" ", strip=True) for title in soup.find_all("title"))
    target = re.sub(r"[\W_]+", "", name).casefold()
    for candidate in candidates:
        words = re.findall(r"[^\W_]+", candidate)
        for start in range(len(words)):
            combined = ""
            for end in range(start, len(words)):
                combined += words[end].casefold()
                if combined == target:
                    # Preserve acronyms and brand capitals supplied by the site itself.
                    return " ".join(word[0].upper() + word[1:] for word in words[start : end + 1])
                if not target.startswith(combined):
                    break
    return None


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


def build_word_diff(old_text: str, new_text: str) -> tuple[Markup, Markup]:
    old_words: list[str] = old_text.split()
    new_words: list[str] = new_text.split()

    matcher = SequenceMatcher(None, old_words, new_words, autojunk=False)

    old_parts: list[str] = []
    new_parts: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            old_parts.extend(escape(word) for word in old_words[i1:i2])
            new_parts.extend(escape(word) for word in new_words[j1:j2])

        elif tag == "delete":
            old_parts.extend(Markup(f'<span class="removed-word">{escape(word)}</span>') for word in old_words[i1:i2])

        elif tag == "insert":
            new_parts.extend(Markup(f'<span class="added-word">{escape(word)}</span>') for word in new_words[j1:j2])

        elif tag == "replace":
            old_parts.extend(Markup(f'<span class="removed-word">{escape(word)}</span>') for word in old_words[i1:i2])
            new_parts.extend(Markup(f'<span class="added-word">{escape(word)}</span>') for word in new_words[j1:j2])

    return (Markup(" ").join(old_parts), Markup(" ").join(new_parts))
