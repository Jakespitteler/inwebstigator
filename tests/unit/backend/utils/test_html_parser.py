from bs4 import BeautifulSoup, Tag

from app.backend.utils.html_parser import (
    PageContent,
    clean_html,
    extract_last_updated,
    extract_links,
    extract_sequential_blocks,
    parse_html,
)


def test_clean_html_removes_unwanted_tags() -> None:
    html_content: str = "<div><script>alert(1)</script><style>body{}</style><p>Hello</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    cleaned: BeautifulSoup = clean_html(soup)

    assert cleaned.find("script") is None
    assert cleaned.find("style") is None
    assert cleaned.find("p") is not None


def test_clean_html_removes_comments() -> None:
    html_content: str = "<div><!-- This is a comment --><p>Content</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    cleaned: BeautifulSoup = clean_html(soup)

    assert "This is a comment" not in str(cleaned)
    assert cleaned.find("p") is not None


def test_clean_html_removes_text_with_html_tags() -> None:
    html_content: str = "<div><span><br></span><p>Valid text</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    cleaned: BeautifulSoup = clean_html(soup)

    assert cleaned.find("p") is not None


def test_extract_links_basic() -> None:
    html_content: str = '<div><a href="https://example.com/page1">Page 1</a><a href="/page2">Page 2</a></div>'
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    links: list[str] = extract_links(container, frozenset(["nav"]))
    assert links == ["https://example.com/page1", "/page2"]


def test_extract_links_ignore_parents() -> None:
    html_content: str = (
        '<div><nav><a href="https://ignore.com">Ignored</a></nav><a href="https://keep.com">Keep</a></div>'
    )
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    links: list[str] = extract_links(container, frozenset(["nav"]))
    assert links == ["https://keep.com"]


def test_extract_last_updated_found() -> None:
    html_content: str = "<div><h2>Last Updated:</h2><p>June 1, 2026</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    last_updated: str | None = extract_last_updated(container)
    assert last_updated == "June 1, 2026"


def test_extract_last_updated_not_found() -> None:
    html_content: str = "<div><h2>Other Heading</h2><p>June 1, 2026</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    last_updated: str | None = extract_last_updated(container)
    assert last_updated is None


def test_extract_sequential_blocks() -> None:
    html_content: str = "<div><h1>Introduction</h1><p>Paragraph text</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    headings, blocks = extract_sequential_blocks(container, frozenset())
    assert headings == ["Introduction"]
    assert len(blocks) == 1
    assert blocks[0].parent_heading == "Introduction"
    assert blocks[0].text == "Paragraph text"


def test_parse_html_full() -> None:
    html_content: str = """
    <html>
        <body>
            <main>
                <h1>Main Title</h1>
                <p>Some main content text.</p>
                <a href="https://example.com">Link</a>
            </main>
        </body>
    </html>
    """
    page_content: PageContent = parse_html(html_content)

    assert page_content.headings == ["Main Title"]
    assert len(page_content.blocks) == 1
    assert page_content.links == ["https://example.com"]
    assert page_content.last_updated is None
