import pytest

from app.backend.crawler.links import (
    extract_links_from_html,
    find_added_links,
    find_removed_links,
    is_document,
    is_internal_web_page,
    separate_document_links,
    website_name,
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


@pytest.mark.parametrize(
    "href",
    [
        "Mailto:someone@example.com",
        "MAILTO:someone@example.com",
        "sms:+61400000000",
        "webcal://example.com/events.ics",
        "ftp://example.com/file",
        "JavaScript:void(0)",
        "tel:+61800000000",
        "data:text/plain,hello",
    ],
)
def test_extract_links_keeps_only_web_links(href: str) -> None:
    """Tests links to anything other than a web page are left out, whatever their case, so one cannot stop a critical
    page being saved."""
    html = f'<a href="{href}">Contact</a><a href="/news">News</a>'

    assert extract_links_from_html(url="https://example.com/", html_content=html) == ["https://example.com/news"]


@pytest.mark.parametrize(
    ("path", "is_page"),
    [
        ("/people/j.smith", True),
        ("/about", True),
        ("/index.html", True),
        ("/v2.0/guide", True),
        ("/files/report.pdf", False),
        ("/images/logo.png", False),
        ("/styles/site.css", False),
        ("/feed.xml", False),
    ],
)
def test_pages_with_a_dot_in_their_name_are_crawled(path: str, is_page: bool) -> None:
    """Tests a page whose name has a dot in it is still a web page, while files such as images are not."""
    assert is_internal_web_page("https://example.com/", f"https://example.com{path}") is is_page


def test_a_page_whose_path_only_starts_with_the_same_letters_is_not_under_the_website() -> None:
    """Tests paths are compared folder by folder, so "/newsletter" is not under a website at "/news"."""
    assert is_internal_web_page("https://example.com/news", "https://example.com/newsletter") is False


def test_found_links_are_listed_once_in_their_original_order() -> None:
    """Tests a link found more than once is reported once, in the order the links were found."""
    current: list[str] = ["https://example.com/c", "https://example.com/a", "https://example.com/c"]

    assert find_added_links([], current) == ["https://example.com/c", "https://example.com/a"]
    assert find_removed_links(current, []) == ["https://example.com/c", "https://example.com/a"]


def test_extract_links_from_html_normalises_each_link() -> None:
    """Tests links written with tracking parameters, a default port or a trailing slash are listed once, without
    them, while a query that changes the page is kept."""
    html = (
        '<a href="/news?utm_source=newsletter">News</a>'
        '<a href="https://example.com:443/news/">News again</a>'
        '<a href="/search?q=fees&amp;fbclid=abc">Search</a>'
    )

    links: list[str] = extract_links_from_html(url="https://example.com/", html_content=html)

    assert links == ["https://example.com/news", "https://example.com/search?q=fees"]


def test_extract_links_from_html_resolves_links_from_the_page_but_keeps_to_the_base_url() -> None:
    """Tests relative links are resolved from the page they are on, while only links under the base URL are kept."""
    html = '<a href="story">Story</a><a href="../contact">Contact</a><a href="/us/news">US news</a>'

    links: list[str] = extract_links_from_html(
        url="https://example.com/au/news/", html_content=html, internal_only=True, base_url="https://example.com/au"
    )

    assert links == ["https://example.com/au/contact", "https://example.com/au/news/story"]


@pytest.mark.parametrize(
    ("url", "expected_name"),
    [
        ("https://www.teqsa.gov.au/", "teqsa.gov.au"),
        ("https://WWW.Example.COM/news", "example.com"),
        ("https://example.com./about", "example.com"),
        ("https://user:password@example.com:8080/page", "example.com"),
        ("https://news.example.com/", "news.example.com"),
    ],
    ids=["www-dropped", "lower-cased", "trailing-dot", "login-and-port", "subdomain-kept"],
)
def test_website_name_is_the_host_without_www(url: str, expected_name: str) -> None:
    """Tests a website is named by its host name, including its domain ending, without a leading "www."."""
    assert website_name(url) == expected_name


@pytest.mark.parametrize("url", ["https://[not-closed/page", "not a url", ""], ids=["unreadable", "no-host", "empty"])
def test_website_name_falls_back_to_website_for_a_url_without_a_host(url: str) -> None:
    """Tests a URL that cannot be read, or has no host name, is shown as "Website" rather than breaking the page."""
    assert website_name(url) == "Website"
