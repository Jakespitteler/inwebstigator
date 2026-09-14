"""
Email notifications for the site scraper.

  changes given      -> digest listing them
  nothing for 7 days -> "no changes detected" note
  otherwise          -> silence

All pure functions:

  - the envelope and the actual sending are in build_message.py
  - state between runs is in app/db/services/notification_service.py

Demo without a scraper or mail server:
    python -m tests.integration.integration_test_email_sender
"""

import difflib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from html import escape, unescape
from typing import Any, Literal, Self, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import config
from app.email_sender.build_message import build_message, send_email

# Client's timezone, not UTC.
REPORT_TIMEZONE = config.report_timezone

try:
    _REPORT_TZ = ZoneInfo(REPORT_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError):
    # A typo in .env shouldn't stop the report going out.
    print(f"  unknown REPORT_TIMEZONE {REPORT_TIMEZONE!r}, falling back to UTC")
    _REPORT_TZ = UTC


def report_tz() -> ZoneInfo:
    """The report timezone. Public for the scheduler."""
    return _REPORT_TZ


def _local(when: datetime) -> datetime:
    """Same instant in the report timezone. Display only, arithmetic stays UTC."""
    return when.astimezone(_REPORT_TZ)


HEARTBEAT_DAYS = 7
# The daily job never fires at the same second, so a strict 7 days slips to 8.
HEARTBEAT_SLACK_HOURS = 12
HEARTBEAT_DUE = timedelta(days=HEARTBEAT_DAYS, hours=-HEARTBEAT_SLACK_HOURS)


# what the diff finder hands over

ChangeType = Literal[
    "PAGE_ADDED",
    "PAGE_CONTENT_CHANGED",
    "PAGE_REMOVED",
    "FILE_ADDED",
    "FILE_CHANGED",
    "FILE_REMOVED",
]

# Order and headings in the email.
SECTIONS = [
    ("PAGE_CONTENT_CHANGED", "Watched pages changed"),
    ("FILE_CHANGED", "Files changed"),
    ("PAGE_ADDED", "New pages"),
    ("FILE_ADDED", "New files"),
    ("PAGE_REMOVED", "Pages no longer reachable"),
    ("FILE_REMOVED", "Files no longer reachable"),
]

# The digest only prints types in SECTIONS, so one without a section would
# vanish from the email. Fail on import instead.
_missing = set(get_args(ChangeType)) ^ {t for t, _ in SECTIONS}
if _missing:
    raise RuntimeError(f"ChangeType and SECTIONS disagree about: {sorted(_missing)}")
del _missing


@dataclass(frozen=True)
class Change:
    """One thing the diff finder noticed. The notifier's only input.

    `label` is a readable name for the URL, falling back to the URL.
    `old_text`/`new_text` build the side by side diff, and are optional: added
    and removed pages have nothing to compare.

    TODO (diff finder): nothing fills in old_text/new_text yet.
    """

    type: ChangeType
    url: str
    label: str | None = None
    old_text: str | None = None
    new_text: str | None = None

    def __post_init__(self):
        if self.type not in get_args(ChangeType):
            raise ValueError(f"unknown change type: {self.type}")

    @property
    def has_diff(self) -> bool:
        """Whether there's enough here for a before/after comparison."""
        return self.old_text is not None and self.new_text is not None

    def to_dict(self) -> dict[str, Any]:
        """A plain dict, for parking this change when a send fails."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild a parked change. Unknown keys are dropped so a deploy doesn't
        strand the retry queue."""
        fields = {"type", "url", "label", "old_text", "new_text"}
        return cls(**{key: value for key, value in data.items() if key in fields})


#  working out what changed inside a page

DIFF_CONTEXT_LINES = 2
# Cap, so one rewritten page can't produce a huge email.
DIFF_MAX_ROWS = 40


def _split_lines(text: str) -> list[str]:
    """Page text into comparable lines. Blanks dropped, they shift around
    whenever the markup changes and make the diff look huge over nothing."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _mark_words(old_line: str, new_line: str) -> tuple[str, str]:
    """Wrap the differing words in <del>/<ins>. Returns HTML."""
    old_words = old_line.split()
    new_words = new_line.split()
    matcher = difflib.SequenceMatcher(None, old_words, new_words)

    left, right = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_part = escape(" ".join(old_words[i1:i2]))
        new_part = escape(" ".join(new_words[j1:j2]))
        if tag == "equal":
            left.append(old_part)
            right.append(new_part)
            continue
        if old_part:
            left.append(f'<del style="background:#ffd7d5;text-decoration:none">{old_part}</del>')
        if new_part:
            right.append(f'<ins style="background:#ccffd8;text-decoration:none">{new_part}</ins>')

    return " ".join(left), " ".join(right)


def diff_rows(old_text: str, new_text: str, context: int = DIFF_CONTEXT_LINES) -> list[tuple[str, str, str]]:
    """Side by side rows of (kind, left_html, right_html).

    `kind` is "equal", "changed", "removed", "added" or "skip", skip being the
    "... unchanged text ..." marker. Escapes as it goes, so callers can drop the
    result straight into a table. Truncated at DIFF_MAX_ROWS.
    """
    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)

    rows: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            block = old_lines[i1:i2]
            if len(block) <= context * 2:
                keep = [(n, n) for n in block]
                skipped = 0
            else:
                keep = [(n, n) for n in block[:context]]
                skipped = len(block) - context * 2
                keep += [(n, n) for n in block[-context:]]
            for k, (left, right) in enumerate(keep):
                if skipped and k == context:
                    rows.append(("skip", f"... {skipped} unchanged lines ...", f"... {skipped} unchanged lines ..."))
                rows.append(("equal", escape(left), escape(right)))
            continue

        if tag == "replace":
            # Pair them up so the client reads before against after.
            for left, right in zip(old_lines[i1:i2], new_lines[j1:j2], strict=False):
                rows.append(("changed", *_mark_words(left, right)))
            # Uneven blocks leave a tail with nothing to pair to.
            extra_old = old_lines[i1 + min(i2 - i1, j2 - j1) : i2]
            extra_new = new_lines[j1 + min(i2 - i1, j2 - j1) : j2]
            rows += [("removed", escape(line), "") for line in extra_old]
            rows += [("added", "", escape(line)) for line in extra_new]
        elif tag == "delete":
            rows += [("removed", escape(line), "") for line in old_lines[i1:i2]]
        elif tag == "insert":
            rows += [("added", "", escape(line)) for line in new_lines[j1:j2]]

    if len(rows) > DIFF_MAX_ROWS:
        dropped = len(rows) - DIFF_MAX_ROWS
        rows = rows[:DIFF_MAX_ROWS]
        rows.append(("skip", f"... {dropped} more rows, see the page itself ...", ""))
    return rows


#  writing the email
# Multipart: HTML plus plain text. The text part is never dropped, some mail
# clients mangle HTML-only email.


def _unified_lines(change: Change) -> list[str]:
    """Before/after for the plain text part, stacked rather than side by side."""
    if not change.has_diff:
        return []

    out = []
    for kind, left, right in diff_rows(change.old_text, change.new_text):
        if kind == "equal":
            continue
        if kind == "skip":
            out.append(left)
        elif kind == "changed":
            out.append(f"- {_strip_tags(left)}")
            out.append(f"+ {_strip_tags(right)}")
        elif kind == "removed":
            out.append(f"- {_strip_tags(left)}")
        else:
            out.append(f"+ {_strip_tags(right)}")
    return out


def _strip_tags(html_fragment: str) -> str:
    """Undo what diff_rows() escaped and marked up, for the text part."""
    text = re.sub(r"<[^>]+>", "", html_fragment)
    return unescape(text)


def render_digest(changes: list[Change], now: datetime, site_name: str) -> tuple[str, str]:
    """(subject, body) for the "what changed" email.

    Buckets by type and prints in SECTIONS order. No db and no network, so call
    it directly when fiddling with wording.
    """
    n = len(changes)
    noun = "change" if n == 1 else "changes"
    local = _local(now)
    subject = f"[{site_name}] {n} {noun} detected - {local:%d %b %Y}"

    lines = [
        f"Website monitoring report for {site_name}",
        f"Checked {local:%d %b %Y, %H:%M %Z}",
        "",
        f"{n} {noun} detected since the last report.",
        "",
    ]

    by_type: dict[str, list[Change]] = {}
    for change in changes:
        by_type.setdefault(change.type, []).append(change)

    for type_, heading in SECTIONS:
        items = by_type.get(type_)
        if not items:
            continue
        lines.append(f"{heading} ({len(items)})")
        for change in items:
            lines.append(f"  * {change.label or change.url}")
            lines.append(f"    {change.url}")
            # No room for two columns in plain text, so the edits stack here.
            for line in _unified_lines(change):
                lines.append(f"      {line}")
        lines.append("")

    lines.append("This is an automated message.")
    return subject, "\n".join(lines)


# Inline styles, not a <style> block. Gmail strips stylesheets out of the head.
_TD = "padding:6px 8px;vertical-align:top;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;width:50%"
_ROW_BG = {"changed": "#fffbe6", "removed": "#ffeceb", "added": "#eaffee", "skip": "#f6f8fa", "equal": "#ffffff"}


def _link(url: str) -> str:
    """Clickable link for http(s), escaped text otherwise.

    Scraped URLs are untrusted, so no <a href> around `javascript:` or `data:`.
    """
    safe = escape(url, quote=True)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{safe}" style="color:#0969da;font-size:12px">{safe}</a>'
    return f'<span style="color:#57606a;font-size:12px">{safe}</span>'


def _diff_table(change: Change) -> str:
    """The side by side before/after table for one changed page."""
    rows = diff_rows(change.old_text, change.new_text)
    # All equal means only whitespace differed, which _split_lines drops.
    if not any(kind not in ("equal", "skip") for kind, _, _ in rows):
        return '<p style="margin:4px 0;color:#57606a;font-size:13px">Text changed, but no visible difference.</p>'

    cells = []
    for kind, left, right in rows:
        colour = "#57606a" if kind == "skip" else "#1f2328"
        cells.append(
            f'<tr style="background:{_ROW_BG[kind]}">'
            f'<td style="{_TD};color:{colour};border-right:1px solid #d0d7de">{left}&nbsp;</td>'
            f'<td style="{_TD};color:{colour}">{right}&nbsp;</td>'
            f"</tr>"
        )

    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" '
        'style="width:100%;max-width:100%;border-collapse:collapse;border:1px solid #d0d7de;margin:8px 0 18px">'
        '<tr style="background:#f6f8fa">'
        '<th style="{h};border-right:1px solid #d0d7de">Before</th>'
        '<th style="{h}">After</th>'
        "</tr>{rows}</table>"
    ).format(
        h="padding:6px 8px;text-align:left;font:600 12px/1.4 system-ui,sans-serif;color:#57606a",
        rows="".join(cells),
    )


def render_digest_html(changes: list[Change], now: datetime, site_name: str) -> str:
    """The HTML half of the digest. Sent alongside the text, never instead."""
    n = len(changes)
    noun = "change" if n == 1 else "changes"

    out = [
        '<div style="font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#1f2328;max-width:900px">',
        f'<h2 style="margin:0 0 4px;font-size:18px">Website monitoring report for {escape(site_name)}</h2>',
        f'<p style="margin:0 0 16px;color:#57606a;font-size:13px">'
        f"Checked {_local(now):%d %b %Y, %H:%M %Z} &middot; "
        f"{n} {noun} detected since the last report.</p>",
    ]

    by_type: dict[str, list[Change]] = {}
    for change in changes:
        by_type.setdefault(change.type, []).append(change)

    for type_, heading in SECTIONS:
        items = by_type.get(type_)
        if not items:
            continue
        out.append(
            f'<h3 style="margin:20px 0 8px;font-size:15px;border-bottom:1px solid #d0d7de;'
            f'padding-bottom:4px">{escape(heading)} ({len(items)})</h3>'
        )
        for change in items:
            label = escape(change.label or change.url)
            out.append(
                f'<p style="margin:10px 0 2px"><strong>{label}</strong><br>{_link(change.url)}</p>'
            )
            if change.has_diff:
                out.append(_diff_table(change))

    out.append('<p style="margin:24px 0 0;color:#57606a;font-size:12px">This is an automated message.</p></div>')
    return "".join(out)


def render_all_clear(now: datetime, since: datetime, site_name: str) -> tuple[str, str]:
    """(subject, body) for the weekly "nothing changed" note.

    `since` is when we last emailed, so the body can name the window.
    """
    local = _local(now)
    since_local = _local(since)
    subject = f"[{site_name}] No changes detected - week to {local:%d %b %Y}"
    body = "\n".join(
        [
            f"Website monitoring report for {site_name}",
            f"Checked {local:%d %b %Y, %H:%M %Z}",
            "",
            f"No changes detected between {since_local:%d %b %Y} and {local:%d %b %Y}.",
            "",
            "Monitoring is running normally.",
            "",
            "This is an automated message.",
        ]
    )
    return subject, body


def notify(
    changes: list[Change],
    site_name: str,
    now: datetime | None = None,
    last_email_at: datetime | None = None,
    dry_run: bool | None = None,
    recipients: list[str] | None = None,
) -> str:
    """Package a run's changes into an email and send it. The way in.

    Call once at the end of each daily run. Returns "digest", "all_clear",
    "nothing" or "failed", for logging.

    `last_email_at`, `site_name` and `recipients` are passed in, not looked up.
    `last_email_at` drives the weekly all-clear: pass None and it never fires,
    since a quiet week and a dead scraper look the same from here.

    Pass `now` to fake the clock in tests.
    """
    now = now or datetime.now(UTC)

    if changes:
        subject, body = render_digest(changes, now, site_name)
        html_body = render_digest_html(changes, now, site_name)
        action = "digest"
    elif last_email_at is not None and now - last_email_at >= HEARTBEAT_DUE:
        subject, body = render_all_clear(now, last_email_at, site_name)
        html_body = None
        action = "all_clear"
    else:
        return "nothing"

    message = build_message(subject, body, html_body, now=now, recipients=recipients)
    return action if send_email(message, dry_run=dry_run) else "failed"
