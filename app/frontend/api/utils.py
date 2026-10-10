import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from bs4.filter import SoupStrainer
from markupsafe import Markup, escape
from pydantic import HttpUrl

from app.backend.diff_checker.word_diff import DiffWord, WordChange, WordDiff, diff_words
from app.models.content_block_models import ChangedBlock, ContentBlock
from app.models.scan_run_models import PageChanges, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteRead

SCAN_STATUS_LABELS: dict[ScanStatus, str] = {
    ScanStatus.SUCCESS: "Scanned",
    ScanStatus.TRAFFIC_ERROR: "Rate limited",
    ScanStatus.CONNECTION_ERROR: "Could not connect",
    ScanStatus.SCAN_ERROR: "Scan failed",
    ScanStatus.SKIPPED_DEACTIVATED: "Skipped, website switched off",
    ScanStatus.SKIPPED_COOLDOWN: "Skipped, on cooldown",
    ScanStatus.TOO_LARGE: "Too large to crawl",
    ScanStatus.PAGES_MISSING: "Most pages missing",
}


def website_card_title(url: str, html: str | None = None) -> str:
    """Writes the friendly title a website's dashboard cards show, e.g. "UWA - Study" for uwa.edu.au/study.

    The name is the part of the domain that identifies the website, written the way the website writes it in its
    saved home page's title or site name where it can be found (e.g. "UWA" rather than "Uwa"), followed by its path.
    Emails and the add website form name websites by `website_name` instead (e.g. "uwa.edu.au/study").

    Args:
        url: The website's URL.
        html: The website's saved home page, or None if it has not been saved yet.

    Returns:
        The title, or "Website" if the URL cannot be read or has no host name.
    """
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
    # These providers host separate sites on subdomains; name the tenant, not the provider.
    hosting_domains = {"vercel.app", "github.io", "netlify.app"}
    if len(labels) >= 3 and (
        ".".join(labels[-2:]) in hosting_domains or (len(labels[-1]) == 2 and labels[-2] in country_categories)
    ):
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
        unquote(section).replace("-", " ").replace("_", " ").strip().title() for section in path.split("/") if section
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


def saved_home_page_html(website: WebsiteRead) -> str | None:
    """Finds the HTML saved for a website's home page, which its card title is read from.

    Args:
        website: The website.

    Returns:
        The saved HTML, or None if the home page has not been saved yet.
    """
    return next(
        (page.text_body for page in website.critical_pages if page.url == website.url and page.text_body),
        None,
    )


def format_timestamp(moment: datetime) -> str:
    """Displays a date and time the same way across the dashboard, in the computer's own time zone, e.g.
    "05 Oct 2026, 09:00". The app's times are in UTC, so they are converted first."""
    # %H rather than %-I, which is not supported on Windows where the desktop app runs
    return moment.astimezone().strftime("%d %b %Y, %H:%M")


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
class PageChangeRecord:
    """What one scan found on one critical page, ready to show on the updates page.

    Attributes:
        url: The critical page.
        changed: Blocks of text that were edited or moved, with the changed words highlighted.
        added: Blocks of text that are new on the page.
        removed: Blocks of text that are no longer on the page.
        links_added: Links that are new on the page.
        links_removed: Links that are no longer on the page.
        documents_added: Documents that are new on the page.
        documents_removed: Documents that are no longer on the page.
        failure_reason: Why the page could not be reached, or None if it was checked.
        failure_count: How many checks of the page have failed in a row.
    """

    url: HttpUrl
    changed: list[TextChangeRecord] = field(default_factory=list[TextChangeRecord])
    added: list[ContentBlockRecord] = field(default_factory=list[ContentBlockRecord])
    removed: list[ContentBlockRecord] = field(default_factory=list[ContentBlockRecord])
    links_added: list[HttpUrl] = field(default_factory=list[HttpUrl])
    links_removed: list[HttpUrl] = field(default_factory=list[HttpUrl])
    documents_added: list[HttpUrl] = field(default_factory=list[HttpUrl])
    documents_removed: list[HttpUrl] = field(default_factory=list[HttpUrl])
    failure_reason: str | None = None
    failure_count: int = 0


@dataclass
class ScanRecord:
    """One scan of a website, ready to show in the website's history on the updates page.

    Attributes:
        scanned_at: When the scan finished.
        status: How the scan went.
        message: What the app did about a scan that did not go normally, e.g. putting the website on cooldown.
        is_awaiting_email: Whether the scan's report has not been emailed yet, e.g. because the mail server was down.
        internal_links_added: Pages found on the website that were not there at the scan before.
        internal_links_removed: Pages from the scan before that are no longer on the website.
        pages: What the scan found on each critical page.
    """

    scanned_at: datetime
    status: ScanStatus
    message: str | None = None
    is_awaiting_email: bool = False
    internal_links_added: list[HttpUrl] = field(default_factory=list[HttpUrl])
    internal_links_removed: list[HttpUrl] = field(default_factory=list[HttpUrl])
    pages: list[PageChangeRecord] = field(default_factory=list[PageChangeRecord])

    @property
    def status_label(self) -> str:
        """A few words saying how the scan went, e.g. "Could not connect"."""
        return SCAN_STATUS_LABELS[self.status]

    @property
    def is_problem(self) -> bool:
        """Whether the scan did not go normally, e.g. the website could not be reached."""
        return self.status is not ScanStatus.SUCCESS

    @property
    def has_details(self) -> bool:
        """Whether the scan found anything, or has a message, worth opening its card for."""
        return bool(self.message or self.internal_links_added or self.internal_links_removed or self.pages)


@dataclass
class WebsiteHistoryRecord:
    """A website's most recent scans, newest first, and what each one found.

    Attributes:
        website_url: The URL of the website.
        scans: The website's most recent scans, newest first.
    """

    website_url: HttpUrl
    scans: list[ScanRecord]

    @property
    def last_scanned_at(self) -> datetime | None:
        """When the website's most recent scan finished, or None if it has not been scanned."""
        return self.scans[0].scanned_at if self.scans else None

    @property
    def scans_with_details(self) -> int:
        """How many of the scans found changes or ran into a problem."""
        return sum(scan.has_details for scan in self.scans)

    @property
    def changed_at(self) -> datetime | None:
        """When the most recent scan that found changes or ran into a problem finished, or None if none did."""
        return next((scan.scanned_at for scan in self.scans if scan.has_details), None)


class ChangeRecord(Protocol):
    """Anything on the updates page that knows when its changes were found."""

    @property
    def changed_at(self) -> datetime | None: ...


def newest_first[RecordT: ChangeRecord](records: Iterable[RecordT]) -> list[RecordT]:
    """Orders change records so the most recently found changes come first.

    Records with no changes have no time, so they go last.

    Args:
        records (Iterable[RecordT]): The website or critical page records to order.

    Returns:
        list[RecordT]: The records, most recent change first.
    """
    return sorted(records, key=lambda record: record.changed_at or datetime.min.replace(tzinfo=UTC), reverse=True)


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


def _content_block_record(block: ContentBlock) -> ContentBlockRecord:
    """Gets a block of added or removed text ready to show on the updates page.

    Args:
        block: The block of text.

    Returns:
        The block's section, text and type.
    """
    return ContentBlockRecord(section=block.parent_heading, text=block.text, block_type=block.block_type.value)


def _text_change_record(change: ChangedBlock) -> TextChangeRecord:
    """Gets an edited or moved block of text ready to show on the updates page, with the changed words highlighted.

    Args:
        change: The block before and after the edit.

    Returns:
        The block's sections and text before and after.
    """
    old_html, new_html = build_word_diff(change.old_block.text, change.new_block.text)
    return TextChangeRecord(
        old_section=change.old_block.parent_heading,
        new_section=change.new_block.parent_heading,
        old=change.old_block.text,
        new=change.new_block.text,
        old_html=old_html,
        new_html=new_html,
        similarity=change.similarity,
    )


def _page_change_record(page: PageChanges) -> PageChangeRecord:
    """Gets what a scan found on one critical page ready to show on the updates page.

    Args:
        page: What the scan found on the page.

    Returns:
        The page's changes.
    """
    return PageChangeRecord(
        url=page.url,
        changed=[_text_change_record(change) for change in page.text_changed],
        added=[_content_block_record(block) for block in page.text_added],
        removed=[_content_block_record(block) for block in page.text_removed],
        links_added=page.links_added,
        links_removed=page.links_removed,
        documents_added=page.documents_added,
        documents_removed=page.documents_removed,
        failure_reason=page.failure_reason,
        failure_count=page.failure_count,
    )


def scan_record(scan_run: ScanRunRead) -> ScanRecord:
    """Gets one scan ready to show in its website's history on the updates page.

    Args:
        scan_run: The scan, with what it found.

    Returns:
        The scan's record.
    """
    return ScanRecord(
        scanned_at=scan_run.scanned_at,
        status=scan_run.status,
        message=scan_run.message,
        is_awaiting_email=scan_run.is_awaiting_email,
        internal_links_added=scan_run.internal_links_added,
        internal_links_removed=scan_run.internal_links_removed,
        pages=[_page_change_record(page) for page in scan_run.pages],
    )


def website_history_record(website_url: HttpUrl, scan_runs: Sequence[ScanRunRead]) -> WebsiteHistoryRecord:
    """Gets a website's most recent scans ready to show on the updates page.

    Args:
        website_url: The URL of the website.
        scan_runs: The website's most recent scans, newest first.

    Returns:
        The website's history.
    """
    return WebsiteHistoryRecord(website_url=website_url, scans=[scan_record(scan_run) for scan_run in scan_runs])
