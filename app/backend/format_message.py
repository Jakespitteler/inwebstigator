import html
from datetime import UTC, datetime
from enum import StrEnum

from app.core.config import config
from app.models.website_models import WebsiteRead


class ScanStatus(StrEnum):
    SUCCESS = "success"
    TRAFFIC_ERROR = "traffic_error"
    CONNECTION_ERROR = "connection_error"
    SKIPPED_DEACTIVATED = "skipped_deactivated"
    SKIPPED_COOLDOWN = "skipped_cooldown"
    TOO_LARGE = "too_large"


def _safe(text: str) -> str:
    return html.escape(text, quote=True)


def _link(url: str) -> str:
    safe_url = _safe(url)
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{safe_url}" class="link-active">{safe_url}</a>'
    return f'<span class="link-inactive">{safe_url}</span>'


def generate_scan_report_html(
    website: WebsiteRead,
    status: ScanStatus = ScanStatus.SUCCESS,
    message: str | None = None,
) -> str:
    """Generates a complete inline-styled HTML report for any scan state or error."""
    now: datetime = datetime.now(UTC)
    safe_url = _safe(website.url)

    # 1. Determine Title and Color Styles Based on Status
    status_config = {
        ScanStatus.SUCCESS: ("Website monitoring report", "#1f2328"),
        ScanStatus.TRAFFIC_ERROR: ("Traffic / Rate Limit Error", "#cf222e"),
        ScanStatus.CONNECTION_ERROR: ("Connection Failure Report", "#cf222e"),
        ScanStatus.SKIPPED_DEACTIVATED: ("Scan Skipped (Deactivated)", "#9a6700"),
        ScanStatus.SKIPPED_COOLDOWN: ("Scan Skipped (Cooldown Active)", "#9a6700"),
        ScanStatus.TOO_LARGE: ("Scan Refused (Website Too Large)", "#9a6700"),
    }
    title_prefix, header_color = status_config.get(status, ("Website Report", "#1f2328"))

    out = [
        '<div class="email-container" style="font-family: system-ui, -apple-system, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; background-color: #ffffff; color: #1f2328; line-height: 1.5; padding: 20px; max-width: 800px; margin: 0 auto; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); border: 1px solid #d0d7de;">',
        f'<h2 class="header-title" style="margin-top: 0; font-size: 20px; color: {header_color}; border-bottom: 1px solid #d0d7de; padding-bottom: 8px;">{title_prefix} for {safe_url}</h2>',
        f'<p class="header-meta" style="margin: 0 0 16px; color: #57606a; font-size: 13px;">Checked {now:%d %b %Y, %H:%M UTC}</p>',
    ]

    # 2. Render Status Alert Box for Non-Success States
    if status != ScanStatus.SUCCESS:
        alert_bg = "#ffeceb" if "error" in status else "#fff8c5"
        alert_border = "rgba(255, 129, 130, 0.4)" if "error" in status else "rgba(212, 167, 44, 0.4)"
        alert_title_color = "#cf222e" if "error" in status else "#9a6700"

        out.extend(
            [
                f'<div style="background-color: {alert_bg}; padding: 12px; border-radius: 6px; border: 1px solid {alert_border}; margin-bottom: 16px;">',
                f'<strong style="display: block; margin-bottom: 4px; color: {alert_title_color};">{title_prefix}</strong>',
                f'<span style="color: #1f2328; font-size: 14px;">{_safe(message or "No further details available.")}</span>',
                "</div>",
                '<hr style="border: 0; height: 1px; background: #d0d7de; margin: 25px 0;">',
                '<p class="footer-text" style="color: #57606a; font-size: 12px; margin: 0;">This is an automated message.</p>',
                "</div>",
            ]
        )
        return "".join(out)

    # 3. Render Changes Diff (Success State)
    has_changes = False

    if website.recent_added_internal_links:
        has_changes = True
        out.append(
            f'<h3 class="section-title" style="font-size: 18px; color: #1f2328; margin-top: 25px;">New Internal Links ({len(website.recent_added_internal_links)})</h3>'
        )
        out.append('<ul class="change-list" style="padding-left: 20px; margin: 0 0 15px 0;">')
        for link in website.recent_added_internal_links:
            out.append(
                f'<li style="margin-bottom: 4px;"><span class="badge added" style="padding: 2px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background-color: #eaffee; color: #1f2328;">+ {_safe(link)}</span></li>'
            )
        out.append("</ul>")

    if website.recent_removed_internal_links:
        has_changes = True
        out.append(
            f'<h3 class="section-title" style="font-size: 18px; color: #1f2328; margin-top: 25px;">Removed Internal Links ({len(website.recent_removed_internal_links)})</h3>'
        )
        out.append('<ul class="change-list" style="padding-left: 20px; margin: 0 0 15px 0;">')
        for link in website.recent_removed_internal_links:
            out.append(
                f'<li style="margin-bottom: 4px;"><span class="badge removed" style="padding: 2px 6px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background-color: #ffeceb; color: #1f2328;">- {_safe(link)}</span></li>'
            )
        out.append("</ul>")

    if website.critical_pages:
        # A page that keeps failing is listed as unreachable, rather than with changes from an earlier scan
        unreachable_pages = [
            cp for cp in website.critical_pages if cp.consecutive_failures >= config.critical_page_alert_after_failures
        ]
        if unreachable_pages:
            has_changes = True
            out.append(
                f'<h3 class="section-title" style="font-size: 18px; color: #cf222e; margin-top: 25px;">Watched Pages Unreachable ({len(unreachable_pages)})</h3>'
            )
            out.append('<ul class="change-list" style="padding-left: 20px; margin: 0 0 15px 0;">')
            for cp in unreachable_pages:
                out.append(
                    f'<li style="margin-bottom: 4px;">{_link(cp.url)}: {_safe(cp.last_failure_reason or "Unknown error")} ({cp.consecutive_failures} scans in a row)</li>'
                )
            out.append("</ul>")

        changed_pages = [
            cp
            for cp in website.critical_pages
            if cp not in unreachable_pages
            and any(
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
                out.append("</div>")

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


def _format_days(days: float) -> str:
    """Formats a day count for display, e.g. 1 -> "day", 7 -> "7 days", 0.5 -> "0.5 days"."""
    return "day" if days == 1 else f"{days:g} days"


def monitoring_started_html(website: WebsiteRead, days_between_health_checks: float) -> str:
    """Generates the HTML body of the email confirming a website is now being monitored.

    An inactive website (e.g. one too large to scan) is said to only have its critical pages watched.

    Args:
        website (WebsiteRead): The website that has started being monitored.
        days_between_health_checks (float): How often the recipient is told nothing has changed.
    """
    critical_pages = "".join(
        f'<li style="margin-bottom: 4px;">{html.escape(critical_page.url)}</li>'
        for critical_page in website.critical_pages
    )
    critical_pages_section = (
        f'<p style="margin: 16px 0 6px;">Pages being watched for content changes:</p>'
        f'<ul style="padding-left: 20px; margin: 0;">{critical_pages}</ul>'
        if critical_pages
        else ""
    )
    inactive_section = (
        ""
        if website.active
        else '<p style="margin: 16px 0 0;">This website is inactive, so only the pages above are checked. '
        "New and removed pages on the rest of the website are not looked for.</p>"
    )
    return (
        "<div style=\"font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; "
        "background-color: #ffffff; color: #1f2328; line-height: 1.5; padding: 20px; max-width: 800px; "
        'margin: 0 auto; border-radius: 8px; border: 1px solid #d0d7de;">'
        '<h2 style="margin-top: 0; font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 8px;">'
        f"Now monitoring {html.escape(website.url)}</h2>"
        '<p style="margin: 0;">You have been added as a recipient for this website. '
        f"It is checked every {_format_days(website.days_between_scans)}, and you will be emailed "
        "when a change is found.</p>"
        f"{critical_pages_section}"
        f"{inactive_section}"
        '<p style="margin: 16px 0 0;">If nothing changes, you will get a short confirmation email every '
        f"{_format_days(days_between_health_checks)} so you know monitoring is still running.</p>"
        '<hr style="border: 0; height: 1px; background: #d0d7de; margin: 25px 0;">'
        '<p style="color: #57606a; font-size: 12px; margin: 0;">This is an automated message.</p>'
        "</div>"
    )
