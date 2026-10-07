import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from ipaddress import ip_address
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, SoupStrainer
from markupsafe import Markup, escape

from app.db.utils.field_types import URLString


def website_name(url: str, html: str | None = None) -> str:
    """Use saved page titles to restore the short name's spacing and capitalisation."""
    try:
        hostname = (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return "Website"
    if not hostname:
        return "Website"

    try:
        ip_address(hostname)
        return hostname
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
            return recognised_name
    return name.replace("-", " ").replace("_", " ").title()


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
