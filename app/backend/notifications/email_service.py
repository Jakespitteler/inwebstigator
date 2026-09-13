from pydantic import SecretStr

from app.db.models.website_models import WebsiteState


def generate_scan_report_body(website_state: WebsiteState) -> str:
    html_parts = [
        f"""<div class="website-scan-report">
            <h2>Scan Report for <a href="{website_state.url}" target="_blank">{website_state.url}</a></h2>
            <p class="report-id"><strong>Website ID:</strong> {website_state.id}</p>"""
    ]

    # Global Website Links Changes
    if website_state.added_internal_links or website_state.removed_internal_links:
        html_parts.append('<div class="global-link-changes"><h3>Global Internal Link Changes</h3><ul>')
        if website_state.added_internal_links:
            for link in website_state.added_internal_links:
                html_parts.append(
                    f'<li class="link-added">Added Link: <a href="{link}" target="_blank">{link}</a></li>'
                )
        if website_state.removed_internal_links:
            for link in website_state.removed_internal_links:
                html_parts.append(f'<li class="link-removed">Removed Link: <span class="strike">{link}</span></li>')
        html_parts.append("</ul></div>")

    # Critical Page States
    if website_state.critical_page_states:
        html_parts.append('<div class="critical-pages-section"><h3>Critical Page Updates</h3>')

        for page in website_state.critical_page_states:
            html_parts.append(
                f"""<div class="critical-page-card" style="border: 1px solid #ccc; margin-bottom: 15px; padding: 15px; border-radius: 6px;">
                    <h4>Page: <a href="{page.url}" target="_blank">{page.url}</a></h4>
                    <small>ID: {page.id}</small>
                """
            )

            # Links added/removed on page
            if page.links_added or page.links_removed:
                html_parts.append('<div class="page-links"><h5>Link Adjustments</h5><ul>')
                if page.links_added:
                    for l in page.links_added:
                        html_parts.append(f'<li class="added">+ Added: <a href="{l}" target="_blank">{l}</a></li>')
                if page.links_removed:
                    for l in page.links_removed:
                        html_parts.append(f'<li class="removed">- Removed: <span class="strike">{l}</span></li>')
                html_parts.append("</ul></div>")

            # Documents added/removed
            if page.documents_added or page.documents_removed:
                html_parts.append('<div class="page-documents"><h5>Document Adjustments</h5><ul>')
                if page.documents_added:
                    for doc in page.documents_added:
                        html_parts.append(f'<li class="added">+ Document Added: {doc}</li>')
                if page.documents_removed:
                    for doc in page.documents_removed:
                        html_parts.append(f'<li class="removed">- Document Removed: {doc}</li>')
                html_parts.append("</ul></div>")

            # Text Added
            if page.text_added:
                html_parts.append('<div class="text-added"><h5>Added Content</h5>')
                for block in page.text_added:
                    html_parts.append(
                        f"""<div class="content-block added-block" style="background-color: #e6ffed; padding: 8px; margin: 4px 0;">
                            <strong>Parent Heading:</strong> {block.parent_heading} <br>
                            <strong>Type:</strong> {block.block_type}
                            <p>{block.text}</p>
                        </div>"""
                    )
                html_parts.append("</div>")

            # Text Removed
            if page.text_removed:
                html_parts.append('<div class="text-removed"><h5>Removed Content</h5>')
                for block in page.text_removed:
                    html_parts.append(
                        f"""<div class="content-block removed-block" style="background-color: #ffeef0; padding: 8px; margin: 4px 0;">
                            <strong>Parent Heading:</strong> {block.parent_heading} <br>
                            <strong>Type:</strong> {block.block_type}
                            <p>{block.text}</p>
                        </div>"""
                    )
                html_parts.append("</div>")

            # Text Changed / Edited Blocks
            if page.text_changed:
                html_parts.append('<div class="text-changed"><h5>Edited Content</h5>')
                for change in page.text_changed:
                    sim_pct = round(change.similarity * 100, 1)
                    html_parts.append(
                        f"""<div class="changed-block" style="border: 1px dashed #f0ad4e; padding: 8px; margin: 4px 0;">
                            <span class="similarity-badge">Similarity: {sim_pct}%</span>
                            <div class="old-version" style="background-color: #f9f2f4; margin-top: 5px; padding: 5px;">
                                <strong>Old ({change.old_block.parent_heading}):</strong> {change.old_block.text}
                            </div>
                            <div class="new-version" style="background-color: #fcf8e3; margin-top: 5px; padding: 5px;">
                                <strong>New ({change.new_block.parent_heading}):</strong> {change.new_block.text}
                            </div>
                        </div>"""
                    )
                html_parts.append("</div>")

            html_parts.append("</div>")  # Close critical-page-card

        html_parts.append("</div>")  # Close critical-pages-section

    html_parts.append("</div>")  # Close website-scan-report
    return "".join(html_parts)


def send_email(email: str, app_password: SecretStr, recipient_email: str, subject: str, body: str) -> None: ...
