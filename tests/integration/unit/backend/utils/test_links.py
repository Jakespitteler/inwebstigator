import pytest

from app.backend.utils.links import (
    extract_links_from_html,
    find_added_links,
    find_link_difference,
    find_removed_links,
    is_document,
    separate_document_links,
)


def test_find_added_links() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page1", "/page2", "/page3"]
    added: list[str] = find_added_links(previous, current)
    assert added == ["/page3"]


def test_find_added_links_no_changes() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page1", "/page2"]
    added: list[str] = find_added_links(previous, current)
    assert added == []


def test_find_removed_links() -> None:
    previous: list[str] = ["/page1", "/page2", "/page3"]
    current: list[str] = ["/page1", "/page2"]
    removed: list[str] = find_removed_links(previous, current)
    assert removed == ["/page3"]


def test_find_removed_links_no_changes() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page1", "/page2"]
    removed: list[str] = find_removed_links(previous, current)
    assert removed == []


def test_find_link_difference() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page2", "/page3"]
    added, removed = find_link_difference(previous, current)
    assert added == ["/page3"]
    assert removed == ["/page1"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/document.pdf",
        "http://example.com/folder/file.DOCX",
        "/downloads/report.xls?download=true",
        "file.ZIP",
    ],
)
def test_is_document_true(url: str) -> None:
    assert is_document(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/page.html",
        "http://example.com/pdf-viewer",
        "/about-us",
        "https://example.com/document.pdf.html",
    ],
)
def test_is_document_false(url: str) -> None:
    assert is_document(url) is False


def test_separate_document_links() -> None:
    links: list[str] = [
        "https://example.com/index.html",
        "https://example.com/resume.pdf",
        "/contact",
        "/docs/manual.docx",
    ]
    docs, non_docs = separate_document_links(links)

    assert set(docs) == {"https://example.com/resume.pdf", "/docs/manual.docx"}
    assert set(non_docs) == {"https://example.com/index.html", "/contact"}


# =============================
# Test extract_links_from_html
# =============================


def test_extract_links_from_html_basic_and_relative(test_url: str) -> None:
    """Test standard absolute, relative links, alphabetical sorting, and uniqueness."""
    html_content: str = """
    <html>
        <body>
            <a href="https://example.com/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/contact">Contact Duplicate</a>
            <a href="https://example.com/services">Services</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content, internal_only=False)

    expected: list[str] = [
        "https://example.com/about",
        "https://example.com/contact",
        "https://example.com/services",
    ]
    assert result == expected


def test_extract_links_from_html_internal_only(test_url: str) -> None:
    """Test filtering for internal links only when internal_only=True."""
    html_content: str = """
    <html>
        <body>
            <a href="/internal-page">Internal Relative</a>
            <a href="https://example.com/another-internal">Internal Absolute</a>
            <a href="https://external.com/page">External Link</a>
            <a href="/policy/policy.pdf">PDF Document</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content, internal_only=True)

    expected: list[str] = [
        "https://example.com/another-internal",
        "https://example.com/internal-page",
    ]
    assert result == expected


def test_extract_links_from_html_skips_non_navigational(test_url: str) -> None:
    """Test that empty strings, hash anchors, javascript, mailto, and tel schemes are ignored."""
    html_content: str = """
    <html>
        <body>
            <a href="">Empty</a>
            <a href="#">Hash Only</a>
            <a href="javascript:void(0);">JS Action</a>
            <a href="mailto:test@example.com">Email</a>
            <a href="tel:+123456789">Phone</a>
            <a href="/valid-page">Valid Page</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content)

    expected: list[str] = ["https://example.com/valid-page"]
    assert result == expected


def test_extract_links_from_html_fragment_stripping(test_url: str) -> None:
    """Test that URL fragments/anchors are stripped and duplicates are consolidated."""
    html_content: str = """
    <html>
        <body>
            <a href="/page#section1">Section 1</a>
            <a href="/page#section2">Section 2</a>
            <a href="/page">Base Page</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content)

    # All variations should collapse to the same clean URL and de-duplicate
    expected: list[str] = ["https://example.com/page"]
    assert result == expected


def test_extract_links_from_html_invalid_url(test_url: str) -> None:
    """Test that ValueError is raised when url lacks a valid netloc/domain."""
    invalid_url: str = "not-a-valid-url"
    html_content: str = '<a href="/page">Page</a>'

    with pytest.raises(ValueError, match="Invalid url provided"):
        extract_links_from_html(invalid_url, html_content)


def test_extract_links_from_html_skips_missing_href(test_url: str) -> None:
    """Test that anchor tags without an href attribute are gracefully ignored."""
    html_content: str = """
    <html>
        <body>
            <a>No Href</a>
            <a name="anchor">Named Anchor</a>
            <a href="/valid">Valid</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content)

    expected: list[str] = ["https://example.com/valid"]
    assert result == expected
