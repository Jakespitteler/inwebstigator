import uuid

from app.backend.format_message import (
    _link,  # pyright: ignore[reportPrivateUsage]
    _safe,  # pyright: ignore[reportPrivateUsage]
    generate_scan_report_html,
)
from app.backend.utils.html_parser import HTMLBlockType
from app.models.critical_page_models import ChangedBlock, ContentBlock, CriticalPageRead
from app.models.website_models import WebsiteRead


def test_safe_escaping() -> None:
    result: str = _safe("<div>Hello & Welcome</div>")
    assert result == "&lt;div&gt;Hello &amp; Welcome&lt;/div&gt;"


def test_safe_escaping_quotes() -> None:
    result: str = _safe('"Quotes" & more')
    assert result == "&quot;Quotes&quot; &amp; more"


def test_link_active_http() -> None:
    url: str = "http://example.com/path?query=1"
    result: str = _link(url)
    assert 'href="http://example.com/path?query=1"' in result
    assert 'class="link-active"' in result


def test_link_active_https() -> None:
    url: str = "https://example.com"
    result: str = _link(url)
    assert 'href="https://example.com"' in result
    assert 'class="link-active"' in result


def test_link_inactive() -> None:
    url: str = "ftp://example.com/file"
    result: str = _link(url)
    assert 'class="link-inactive"' in result
    assert "ftp://example.com/file" in result


def test_generate_scan_report_html_no_changes() -> None:
    state: WebsiteRead = WebsiteRead(id=uuid.uuid4(), user_id=uuid.uuid4(), url="https://example.com")
    html_output: str = generate_scan_report_html(state)

    assert "No changes detected since the last scan" in html_output
    assert "Website monitoring report for https://example.com" in html_output


def test_generate_scan_report_html_with_internal_links() -> None:
    state: WebsiteRead = WebsiteRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        url="https://example.com",
        recent_added_internal_links=["https://example.com/page-one"],
        recent_removed_internal_links=["https://example.com/page-two"],
    )
    html_output: str = generate_scan_report_html(state)

    assert "New Internal Links (1)" in html_output
    assert "/page-one" in html_output
    assert "Removed Internal Links (1)" in html_output
    assert "/page-two" in html_output


def test_generate_scan_report_html_with_critical_pages() -> None:
    block_added = ContentBlock(text="New content added", parent_heading="Section A", block_type=HTMLBlockType.PARAGRAPH)
    block_removed = ContentBlock(text="Old content removed", block_type=HTMLBlockType.PARAGRAPH)
    old_change = ContentBlock(text="Before text", parent_heading="Changes", block_type=HTMLBlockType.PARAGRAPH)
    new_change = ContentBlock(text="After text", parent_heading="Changes", block_type=HTMLBlockType.PARAGRAPH)

    critical_page: CriticalPageRead = CriticalPageRead(
        id=uuid.uuid4(),
        url="https://example.com/critical",
        website_id=uuid.uuid4(),
        recent_links_added=["https://external.com"],
        recent_links_removed=["https://old-external.com"],
        recent_documents_added=["https://example.com/critical/doc.pdf"],
        recent_documents_removed=["https://example.com/critical/old.pdf"],
        recent_text_added=[block_added],
        recent_text_removed=[block_removed],
        recent_text_changed=[ChangedBlock(old_block=old_change, new_block=new_change, similarity=0.5)],
    )

    website: WebsiteRead = WebsiteRead(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        url="https://example.com",
        critical_pages=[critical_page],
    )

    html_output: str = generate_scan_report_html(website)

    assert "Watched Pages Changed (1)" in html_output
    assert "Links Added:" in html_output
    assert "Links Removed:" in html_output
    assert "Documents Added:" in html_output
    assert "Documents Removed:" in html_output
    assert "Text Added:" in html_output
    assert "[Section A] " in html_output
    assert "New content added" in html_output
    assert "Text Changed:" in html_output
    assert "Before" in html_output
    assert "After" in html_output
