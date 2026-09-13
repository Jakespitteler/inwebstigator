import html
from datetime import UTC, datetime

from app.db.models.website_models import WebsiteState


def _safe(text: str) -> str:
    """Safely escape scraped text for HTML insertion."""
    return html.escape(text, quote=True)


def _link(url: str) -> str:
    """Creates a hyperlink with semantic classes."""
    safe_url = _safe(url)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{safe_url}" class="link-active">{safe_url}</a>'
    return f'<span class="link-inactive">{safe_url}</span>'


def generate_scan_report_html(website_state: WebsiteState) -> str:
    """
    Formats the website state into a clean HTML structure with CSS classes.
    """
    now = datetime.now(UTC)

    out = [
        '<div class="email-container">',
        f'<h2 class="header-title">Website monitoring report for {_safe(website_state.url)}</h2>',
        f'<p class="header-meta">Checked {now:%d %b %Y, %H:%M UTC}</p>',
    ]

    has_changes = False

    # --- 1. Site-wide Internal Links ---
    if website_state.added_internal_links:
        has_changes = True
        out.append(f'<h3 class="section-title">New Internal Links ({len(website_state.added_internal_links)})</h3>')
        out.append('<ul class="change-list">')
        for link in website_state.added_internal_links:
            out.append(f'<li><span class="badge added">+ {_safe(link)}</span></li>')
        out.append("</ul>")

    if website_state.removed_internal_links:
        has_changes = True
        out.append(
            f'<h3 class="section-title">Removed Internal Links ({len(website_state.removed_internal_links)})</h3>'
        )
        out.append('<ul class="change-list">')
        for link in website_state.removed_internal_links:
            out.append(f'<li><span class="badge removed">- {_safe(link)}</span></li>')
        out.append("</ul>")

    # --- 2. Critical Pages ---
    if website_state.critical_page_states:
        changed_pages = [
            cp
            for cp in website_state.critical_page_states
            if any(
                [
                    cp.links_added,
                    cp.links_removed,
                    cp.documents_added,
                    cp.documents_removed,
                    cp.text_added,
                    cp.text_removed,
                    cp.text_changed,
                ]
            )
        ]

        if changed_pages:
            has_changes = True
            out.append(f'<h3 class="section-title">Watched Pages Changed ({len(changed_pages)})</h3>')

            for cp in changed_pages:
                out.append(f'<p class="page-title"><strong>{_safe(cp.url)}</strong><br>{_link(cp.url)}</p>')
                out.append('<div class="page-changes-container">')

                # Links & Documents
                for label, items, badge_class, symbol in [
                    ("Links Added", cp.links_added, "added", "+"),
                    ("Links Removed", cp.links_removed, "removed", "-"),
                    ("Documents Added", cp.documents_added, "added", "+"),
                    ("Documents Removed", cp.documents_removed, "removed", "-"),
                ]:
                    if items:
                        out.append(f'<p class="change-label">{label}:</p><ul class="change-list">')
                        for item in items:
                            out.append(f'<li><span class="badge {badge_class}">{symbol} {_safe(item)}</span></li>')
                        out.append("</ul>")

                # Text Added / Removed
                for label, blocks, badge_class, symbol in [
                    ("Text Added", cp.text_added, "added", "+"),
                    ("Text Removed", cp.text_removed, "removed", "-"),
                ]:
                    if blocks:
                        out.append(f'<p class="change-label">{label}:</p><ul class="change-list">')
                        for block in blocks:
                            heading = f"[{_safe(block.parent_heading)}] " if block.parent_heading else ""
                            out.append(
                                f'<li><div class="text-block {badge_class}"><strong>{heading}</strong>{_safe(block.text)}</div></li>'
                            )
                        out.append("</ul>")

                # Text Changed (Side-by-Side Table)
                if cp.text_changed:
                    out.append('<p class="change-label">Text Changed:</p>')
                    out.append('<table class="diff-table" role="presentation" cellspacing="0" cellpadding="0">')
                    out.append('<tr class="diff-header"><th>Before</th><th>After</th></tr>')

                    for change in cp.text_changed:
                        heading = (
                            f"[{_safe(change.new_block.parent_heading)}] " if change.new_block.parent_heading else ""
                        )
                        if heading:
                            out.append(f'<tr><td colspan="2" class="diff-heading-row">{heading}</td></tr>')

                        out.append("<tr>")
                        out.append(f'<td class="diff-cell removed">- {_safe(change.old_block.text)}</td>')
                        out.append(f'<td class="diff-cell added">+ {_safe(change.new_block.text)}</td>')
                        out.append("</tr>")

                    out.append("</table>")

                out.append("</div>")  # Close page container

    # --- 3. Handle No Changes ---
    if not has_changes:
        out[2] = (
            f'<p class="header-meta">Checked {now:%d %b %Y, %H:%M UTC} &middot; No changes detected since the last scan.</p>'
        )

    out.append('<p class="footer-text">This is an automated message.</p>')
    out.append("</div>")

    return "".join(out)
