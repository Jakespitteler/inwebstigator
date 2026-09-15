import html
from datetime import UTC, datetime

from app.models.website_models import WebsiteRead


def _safe(text: str) -> str:
    """Safely escape scraped text for HTML insertion."""
    return html.escape(text, quote=True)


def _link(url: str) -> str:
    """Creates a hyperlink with semantic classes."""
    safe_url = _safe(url)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{safe_url}" class="link-active">{safe_url}</a>'
    return f'<span class="link-inactive">{safe_url}</span>'


def generate_scan_report_html(Website: WebsiteRead) -> str:
    """
    Formats the website state into a clean HTML structure with inline CSS styles for email compatibility.
    """
    now: datetime = datetime.now(UTC)

    out = [
        '<div class="email-container" style="font-family: system-ui, -apple-system, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; background-color: #f6f8fa; color: #1f2328; line-height: 1.5; padding: 20px; max-width: 800px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border: 1px solid #d0d7de;">',
        f'<h2 class="header-title" style="margin-top: 0; font-size: 20px; color: #1f2328; border-bottom: 1px solid #d0d7de; padding-bottom: 8px;">Website monitoring report for {_safe(Website.url)}</h2>',
        f'<p class="header-meta" style="margin: 0 0 16px; color: #57606a; font-size: 13px;">Checked {now:%d %b %Y, %H:%M UTC}</p>',
    ]

    has_changes = False

    # --- 1. Site-wide Internal Links ---
    if Website.recent_added_internal_links:
        has_changes = True
        out.append(
            f'<h3 class="section-title" style="font-size: 18px; color: #1f2328; margin-top: 25px;">New Internal Links ({len(Website.recent_added_internal_links)})</h3>'
        )
        out.append('<ul class="change-list" style="padding-left: 20px; margin: 0 0 15px 0;">')
        for link in Website.recent_added_internal_links:
            out.append(
                f'<li style="margin-bottom: 4px;"><span class="badge added" style="padding: 2px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background-color: #eaffee; color: #1f2328;">+ {_safe(link)}</span></li>'
            )
        out.append("</ul>")

    if Website.recent_removed_internal_links:
        has_changes = True
        out.append(
            f'<h3 class="section-title" style="font-size: 18px; color: #1f2328; margin-top: 25px;">Removed Internal Links ({len(Website.recent_removed_internal_links)})</h3>'
        )
        out.append('<ul class="change-list" style="padding-left: 20px; margin: 0 0 15px 0;">')
        for link in Website.recent_removed_internal_links:
            out.append(
                f'<li style="margin-bottom: 4px;"><span class="badge removed" style="padding: 2px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background-color: #ffeceb; color: #1f2328;">- {_safe(link)}</span></li>'
            )
        out.append("</ul>")

    # --- 2. Critical Pages ---
    if Website.critical_pages:
        changed_pages = [
            cp
            for cp in Website.critical_pages
            if any(
                [
                    cp.recent_links_added,
                    cp.recent_links_removed,
                    cp.recent_documents_added,
                    cp.recent_documents_removed,
                    cp.recent_text_added,
                    cp.recent_text_removed,
                    cp.recent_text_changed,
                ]
            )
        ]

        if changed_pages:
            has_changes = True
            out.append(
                f'<h3 class="section-title" style="font-size: 18px; color: #1f2328; margin-top: 25px;">Watched Pages Changed ({len(changed_pages)})</h3>'
            )

            for cp in changed_pages:
                out.append(
                    f'<p class="page-title" style="margin: 16px 0 8px;"><strong style="color: #1f2328;">{_safe(cp.url)}</strong><br>{_link(cp.url)}</p>'
                )
                out.append('<div class="page-changes-container" style="margin-bottom: 20px;">')

                # Links & Documents
                for label, items, badge_class, symbol in [
                    ("Links Added", cp.recent_links_added, "added", "+"),
                    ("Links Removed", cp.recent_links_removed, "removed", "-"),
                    ("Documents Added", cp.recent_documents_added, "added", "+"),
                    ("Documents Removed", cp.recent_documents_removed, "removed", "-"),
                ]:
                    if items:
                        bg_col = "#eaffee" if badge_class == "added" else "#ffeceb"
                        out.append(
                            f'<p class="change-label" style="margin: 8px 0 4px; font-weight: 600; color: #57606a;">{label}:</p><ul class="change-list" style="padding-left: 20px; margin: 0 0 10px 0;">'
                        )
                        for item in items:
                            out.append(
                                f'<li style="margin-bottom: 4px;"><span class="badge {badge_class}" style="padding: 2px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background-color: {bg_col}; color: #1f2328;">{symbol} {_safe(item)}</span></li>'
                            )
                        out.append("</ul>")

                # Text Added / Removed
                for label, blocks, badge_class, symbol in [
                    ("Text Added", cp.recent_text_added, "added", "+"),
                    ("Text Removed", cp.recent_text_removed, "removed", "-"),
                ]:
                    if blocks:
                        bg_col = "#eaffee" if badge_class == "added" else "#ffeceb"
                        out.append(
                            f'<p class="change-label" style="margin: 8px 0 4px; font-weight: 600; color: #57606a;">{label}:</p><ul class="change-list" style="padding-left: 20px; margin: 0 0 10px 0;">'
                        )
                        for block in blocks:
                            heading = f"[{_safe(block.parent_heading)}] " if block.parent_heading else ""
                            out.append(
                                f'<li style="margin-bottom: 4px;"><div class="text-block {badge_class}" style="padding: 6px 8px; border-radius: 4px; display: inline-block; margin-bottom: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; border: 1px solid rgba(0,0,0,0.05); background-color: {bg_col}; color: #1f2328;"><strong style="color: #1f2328;">{heading}</strong>{_safe(block.text)}</div></li>'
                            )
                        out.append("</ul>")

                # Text Changed (Side-by-Side Table)
                if cp.recent_text_changed:
                    out.append(
                        '<p class="change-label" style="margin: 8px 0 4px; font-weight: 600; color: #57606a;">Text Changed:</p>'
                    )
                    out.append(
                        '<table class="diff-table" role="presentation" cellspacing="0" cellpadding="0" style="width: 100%; border-collapse: collapse; border: 1px solid #d0d7de; margin: 4px 0 16px; border-radius: 6px; overflow: hidden;">'
                    )
                    out.append(
                        '<tr class="diff-header"><th style="background-color: #f6f8fa; padding: 8px; text-align: left; font-weight: 600; font-size: 12px; color: #57606a; width: 50%; border-bottom: 1px solid #d0d7de; border-right: 1px solid #d0d7de;">Before</th><th style="background-color: #f6f8fa; padding: 8px; text-align: left; font-weight: 600; font-size: 12px; color: #57606a; width: 50%; border-bottom: 1px solid #d0d7de;">After</th></tr>'
                    )

                    for change in cp.recent_text_changed:
                        heading = (
                            f"[{_safe(change.new_block.parent_heading)}] " if change.new_block.parent_heading else ""
                        )
                        if heading:
                            out.append(
                                f'<tr><td colspan="2" class="diff-heading-row" style="background-color: #f6f8fa; padding: 6px 8px; font-size: 12px; color: #57606a; border-bottom: 1px solid #d0d7de;">{heading}</td></tr>'
                            )

                        out.append("<tr>")
                        out.append(
                            f'<td class="diff-cell removed" style="padding: 8px; vertical-align: top; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; line-height: 1.45; border-right: 1px solid #d0d7de; background-color: #ffeceb; color: #1f2328;">- {_safe(change.old_block.text)}</td>'
                        )
                        out.append(
                            f'<td class="diff-cell added" style="padding: 8px; vertical-align: top; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; line-height: 1.45; background-color: #eaffee; color: #1f2328;">+ {_safe(change.new_block.text)}</td>'
                        )
                        out.append("</tr>")

                    out.append("</table>")

                out.append("</div>")  # Close page container

    # --- 3. Handle No Changes ---
    if not has_changes:
        out[2] = (
            f'<p class="header-meta" style="margin: 0 0 16px; color: #57606a; font-size: 13px;">Checked {now:%d %b %Y, %H:%M UTC} &middot; No changes detected since the last scan.</p>'
        )

    out.append('<hr style="border: 0; height: 1px; background: #d0d7de; margin: 25px 0;">')
    out.append(
        '<p class="footer-text" style="color: #57606a; font-size: 12px; margin: 0;">This is an automated message.</p>'
    )
    out.append("</div>")

    return "".join(out)
