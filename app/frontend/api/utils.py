from dataclasses import dataclass, field
from difflib import SequenceMatcher

from markupsafe import Markup, escape

from app.db.utils.field_types import URLString


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
