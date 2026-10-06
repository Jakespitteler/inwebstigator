import pytest

from app.backend.utils.links import (
    extract_links_from_html,
    find_added_links,
    find_removed_links,
    is_document,
    is_internal_web_page,
    is_same_page,
    page_key,
    remove_repeated_pages,
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


@pytest.mark.parametrize(
    "url",
    [
        "{test_url}document.pdf",
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
        "{test_url}page.html",
        "http://example.com/pdf-viewer",
        "/about-us",
        "{test_url}document.pdf.html",
    ],
)
def test_is_document_false(url: str) -> None:
    assert is_document(url) is False


def test_separate_document_links() -> None:
    links: list[str] = [
        "{test_url}index.html",
        "{test_url}resume.pdf",
        "/contact",
        "/docs/manual.docx",
    ]
    docs, non_docs = separate_document_links(links)

    assert set(docs) == {"{test_url}resume.pdf", "/docs/manual.docx"}
    assert set(non_docs) == {"{test_url}index.html", "/contact"}


# =============================
# Test extract_links_from_html
# =============================


def test_extract_links_from_html_basic_and_relative(test_url: str) -> None:
    """Test standard absolute, relative links, alphabetical sorting, and uniqueness."""
    html_content: str = f"""
    <html>
        <body>
            <a href="{test_url}about">About</a>
            <a href="/contact">Contact</a>
            <a href="/contact">Contact Duplicate</a>
            <a href="{test_url}services">Services</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content, internal_only=False)

    expected: list[str] = [
        f"{test_url}about",
        f"{test_url}contact",
        f"{test_url}services",
    ]
    assert result == expected


def test_extract_links_from_html_internal_only(test_url: str) -> None:
    """Test filtering for internal links only when internal_only=True."""
    html_content: str = f"""
    <html>
        <body>
            <a href="/internal-page">Internal Relative</a>
            <a href="{test_url}another-internal">Internal Absolute</a>
            <a href="https://external.com/page">External Link</a>
            <a href="/policy/policy.pdf">PDF Document</a>
        </body>
    </html>
    """
    result: list[str] = extract_links_from_html(test_url, html_content, internal_only=True)

    expected: list[str] = [
        f"{test_url}another-internal",
        f"{test_url}internal-page",
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

    expected: list[str] = [f"{test_url}valid-page"]
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
    expected: list[str] = [f"{test_url}page"]
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

    expected: list[str] = [f"{test_url}valid"]
    assert result == expected


@pytest.mark.parametrize(
    ("base_url", "check_url", "expected"),
    [
        ("https://example.com/", "https://www.example.com/page", True),
        ("https://www.example.com/", "https://example.com/page", True),
        ("https://Example.COM/", "https://www.example.com/page", True),
        ("https://example.com/news", "https://www.example.com/news/story", True),
        ("https://example.com/", "https://news.example.com/page", False),
        ("https://example.com/", "https://www.other.com/page", False),
        ("https://example.com/news", "https://www.example.com/sport", False),
    ],
)
def test_is_internal_web_page_treats_www_as_same_site(base_url: str, check_url: str, expected: bool) -> None:
    """Test example.com and www.example.com count as one website, but other subdomains and sites do not."""
    assert is_internal_web_page(base_url, check_url) is expected


@pytest.mark.parametrize(
    ("url", "other_url", "expected"),
    [
        ("https://example.com", "https://example.com/", True),
        ("https://example.com/news", "https://example.com/news/", True),
        ("https://example.com/news#latest", "https://example.com/news", True),
        ("https://www.example.com/news", "https://example.com/news", True),
        ("https://Example.COM/news", "https://example.com/news", True),
        ("https://WWW.Example.com/news/", "https://example.com/news", True),
        ("https://example.com/News", "https://example.com/news", False),
        ("https://example.com/news", "https://example.com/sport", False),
        ("https://example.com/news?page=2", "https://example.com/news", False),
        ("https://news.example.com/", "https://example.com/", False),
    ],
)
def test_is_same_page_ignores_differences_that_do_not_change_the_page(url: str, other_url: str, expected: bool) -> None:
    """Test URLs that only differ by a trailing slash, fragment, leading "www." or capitals in the domain are the
    same page, while a different path (including its capitals), query or subdomain is a different page."""
    assert is_same_page(url, other_url) is expected


def test_remove_repeated_pages_keeps_the_first_of_each_page() -> None:
    """Test a page written a second time, e.g. with a trailing slash, is removed, keeping the order and the first
    way it was written."""
    urls = [
        "https://example.com",
        "https://example.com/news/",
        "https://www.example.com/",
        "https://Example.com/news",
    ]

    assert remove_repeated_pages(urls) == ["https://example.com", "https://example.com/news/"]


def test_page_key_only_removes_differences_that_do_not_change_the_page() -> None:
    """Test the page key drops a leading "www.", capitals in the domain, a trailing slash and a fragment, but keeps
    the path's capitals and the query, which can change the page."""
    assert page_key("https://WWW.Example.com/News/?id=1#top") == "https://example.com/News?id=1"
    assert page_key("https://www.example.com") == "https://example.com/"


def test_links_written_differently_for_the_same_page_are_not_added_or_removed() -> None:
    """Test a link now written with or without "www." or with different capitals in the domain is not reported as
    a page added or removed, while real changes still are."""
    previous = ["https://example.com/news", "https://example.com/about"]
    current = ["https://www.example.com/news", "https://Example.com/about", "https://example.com/contact"]

    assert find_added_links(previous, current) == ["https://example.com/contact"]
    assert find_removed_links(previous, current) == []
    assert find_removed_links(current, previous) == ["https://example.com/contact"]


def test_extract_links_from_html_lists_each_page_once() -> None:
    """Test a page linked as both example.com and www.example.com is only listed once, and domains are written in
    lower case."""
    html = (
        '<a href="https://www.example.com/news">News</a>'
        '<a href="https://example.com/news/">News again</a>'
        '<a href="https://EXAMPLE.com/about">About</a>'
    )

    links = extract_links_from_html(url="https://example.com/", html_content=html)

    assert links == ["https://example.com/about", "https://example.com/news"]
