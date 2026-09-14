from app.notifier.format_message import _link, _safe, generate_scan_report_html  # pyright: ignore[reportPrivateUsage]


class DummyTextBlock:
    def __init__(self, text: str, parent_heading: str | None = None) -> None:
        self.text: str = text
        self.parent_heading: str | None = parent_heading


class DummyTextChange:
    def __init__(self, old_block: DummyTextBlock, new_block: DummyTextBlock) -> None:
        self.old_block: DummyTextBlock = old_block
        self.new_block: DummyTextBlock = new_block


class DummyCriticalPageState:
    def __init__(
        self,
        url: str,
        links_added: list[str] | None = None,
        links_removed: list[str] | None = None,
        documents_added: list[str] | None = None,
        documents_removed: list[str] | None = None,
        text_added: list[DummyTextBlock] | None = None,
        text_removed: list[DummyTextBlock] | None = None,
        text_changed: list[DummyTextChange] | None = None,
    ) -> None:
        self.url: str = url
        self.links_added: list[str] = links_added or []
        self.links_removed: list[str] = links_removed or []
        self.documents_added: list[str] = documents_added or []
        self.documents_removed: list[str] = documents_removed or []
        self.text_added: list[DummyTextBlock] = text_added or []
        self.text_removed: list[DummyTextBlock] = text_removed or []
        self.text_changed: list[DummyTextChange] = text_changed or []


class DummyWebsiteState:
    def __init__(
        self,
        url: str,
        added_internal_links: list[str] | None = None,
        removed_internal_links: list[str] | None = None,
        critical_page_states: list[DummyCriticalPageState] | None = None,
    ) -> None:
        self.url: str = url
        self.added_internal_links: list[str] = added_internal_links or []
        self.removed_internal_links: list[str] = removed_internal_links or []
        self.critical_page_states: list[DummyCriticalPageState] = critical_page_states or []


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
    state: DummyWebsiteState = DummyWebsiteState(url="https://example.com")
    html_output: str = generate_scan_report_html(state)  # type: ignore

    assert "No changes detected since the last scan" in html_output
    assert "Website monitoring report for https://example.com" in html_output


def test_generate_scan_report_html_with_internal_links() -> None:
    state: DummyWebsiteState = DummyWebsiteState(
        url="https://example.com",
        added_internal_links=["/page-one"],
        removed_internal_links=["/page-two"],
    )
    html_output: str = generate_scan_report_html(state)  # type: ignore

    assert "New Internal Links (1)" in html_output
    assert "/page-one" in html_output
    assert "Removed Internal Links (1)" in html_output
    assert "/page-two" in html_output


def test_generate_scan_report_html_with_critical_pages() -> None:
    block_added: DummyTextBlock = DummyTextBlock(text="New content added", parent_heading="Section A")
    block_removed: DummyTextBlock = DummyTextBlock(text="Old content removed")
    old_change: DummyTextBlock = DummyTextBlock(text="Before text", parent_heading="Changes")
    new_change: DummyTextBlock = DummyTextBlock(text="After text", parent_heading="Changes")

    cp: DummyCriticalPageState = DummyCriticalPageState(
        url="https://example.com/critical",
        links_added=["https://external.com"],
        links_removed=["https://old-external.com"],
        documents_added=["doc.pdf"],
        documents_removed=["old.pdf"],
        text_added=[block_added],
        text_removed=[block_removed],
        text_changed=[DummyTextChange(old_block=old_change, new_block=new_change)],
    )

    state: DummyWebsiteState = DummyWebsiteState(
        url="https://example.com",
        critical_page_states=[cp],
    )

    html_output: str = generate_scan_report_html(state)  # type: ignore

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
