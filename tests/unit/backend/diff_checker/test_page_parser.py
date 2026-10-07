from bs4 import BeautifulSoup, Tag

from app.backend.diff_checker.content_diff import compare_page_content
from app.backend.diff_checker.models import HTMLBlockType, PageContent
from app.backend.diff_checker.page_parser import (
    clean_html,
    extract_last_updated,
    extract_sequential_blocks,
    normalize_text,
    parse_html,
    parse_standard_text,
    parse_table_row,
)


def test_normalize_text_basic() -> None:
    raw: str = "  Hello   \n  world!  "
    assert normalize_text(raw) == "Hello world!"


def test_normalize_text_empty() -> None:
    assert normalize_text("   \n\t  ") == ""


def test_parse_standard_text() -> None:
    html: str = "<div>  Some\n  text  </div>"
    soup: BeautifulSoup = BeautifulSoup(html, "html.parser")
    tag: Tag = soup.find("div")  # type: ignore
    assert parse_standard_text(tag) == "Some text"


def test_parse_table_row() -> None:
    html: str = "<tr><th>Header 1</th><td>Cell 1</td><td>  Cell\n2  </td></tr>"
    soup: BeautifulSoup = BeautifulSoup(html, "html.parser")
    tag: Tag = soup.find("tr")  # type: ignore
    assert parse_table_row(tag) == "Header 1 | Cell 1 | Cell 2"


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
    assert [(block.parent_heading, block.block_type, block.text) for block in blocks] == [
        ("No heading", HTMLBlockType.HEADING_1, "Introduction"),
        ("Introduction", HTMLBlockType.PARAGRAPH, "Paragraph text"),
    ]


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
    assert [block.text for block in page_content.blocks] == ["Main Title", "Some main content text.", "Link"]
    assert page_content.last_updated is None


def test_extract_sequential_blocks_captures_list_items() -> None:
    html_content: str = "<div><h2>Fees</h2><ul><li>Application fee: $100</li><li>Late fee: $20</li></ul></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    _, blocks = extract_sequential_blocks(container, frozenset())
    assert [(block.parent_heading, block.block_type, block.text) for block in blocks] == [
        ("No heading", HTMLBlockType.HEADING_2, "Fees"),
        ("Fees", HTMLBlockType.LIST_ITEM, "Application fee: $100"),
        ("Fees", HTMLBlockType.LIST_ITEM, "Late fee: $20"),
    ]


def test_extract_sequential_blocks_still_captures_blockquotes() -> None:
    html_content: str = "<div><blockquote>Quoted policy text</blockquote></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    _, blocks = extract_sequential_blocks(container, frozenset())
    assert [(block.block_type, block.text) for block in blocks] == [(HTMLBlockType.QUOTE, "Quoted policy text")]


def test_extract_sequential_blocks_does_not_duplicate_nested_blocks() -> None:
    html_content: str = (
        "<div>"
        "<ul><li><p>Paragraph inside a list item</p></li></ul>"
        "<ol><li>Parent item<ul><li>Child item</li></ul></li></ol>"
        "<blockquote><p>Paragraph inside a quote</p></blockquote>"
        "</div>"
    )
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    _, blocks = extract_sequential_blocks(container, frozenset())
    assert [block.text for block in blocks] == [
        "Paragraph inside a list item",
        "Parent item Child item",
        "Paragraph inside a quote",
    ]


def test_extract_sequential_blocks_does_not_duplicate_headings_nested_in_blocks() -> None:
    html_content: str = "<div><ul><li><h3>Step 1</h3>Fill in the form</li></ul><p>Then submit it</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    headings, blocks = extract_sequential_blocks(container, frozenset())
    assert headings == ["Step 1"]
    assert [(block.parent_heading, block.text) for block in blocks] == [
        ("No heading", "Step 1 Fill in the form"),
        ("Step 1", "Then submit it"),
    ]


def test_list_item_edit_is_detected_as_a_change() -> None:
    old_page: PageContent = parse_html("<main><h2>Fees</h2><ul><li>Application fee: $100</li></ul></main>")
    new_page: PageContent = parse_html("<main><h2>Fees</h2><ul><li>Application fee: $120</li></ul></main>")

    added, removed, changed = compare_page_content(old_page, new_page)
    assert (added, removed) == ([], [])
    assert len(changed) == 1
    assert changed[0].old_block.text == "Application fee: $100"
    assert changed[0].new_block.text == "Application fee: $120"


def blocks_of(html: str) -> list[tuple[HTMLBlockType, str]]:
    """Parses a page and lists each block's type and text."""
    return [(block.block_type, block.text) for block in parse_html(html).blocks]


def test_loose_text_in_containers_is_read() -> None:
    """Regression: text straight inside a <div>, <section> or <span> was never read, so its changes were missed."""
    assert blocks_of("<main><div>Application fee is $50.</div><section>Office closed on Friday</section></main>") == [
        (HTMLBlockType.TEXT, "Application fee is $50."),
        (HTMLBlockType.TEXT, "Office closed on Friday"),
    ]


def test_text_and_inline_tags_next_to_each_other_are_one_block() -> None:
    assert blocks_of("<main><div>Fee: <strong>$50</strong> per <a href='/year'>year</a></div></main>") == [
        (HTMLBlockType.TEXT, "Fee: $50 per year")
    ]


def test_loose_text_around_a_block_is_read_separately() -> None:
    assert blocks_of("<main><div>Before <p>Inside</p> after</div></main>") == [
        (HTMLBlockType.TEXT, "Before"),
        (HTMLBlockType.PARAGRAPH, "Inside"),
        (HTMLBlockType.TEXT, "after"),
    ]


def test_definition_lists_code_and_captions_are_read() -> None:
    html = (
        "<main><dl><dt>Fee</dt><dd>$50</dd></dl><pre>fee = 50</pre>"
        "<figure><figcaption>Fee table</figcaption></figure></main>"
    )

    assert blocks_of(html) == [
        (HTMLBlockType.DEFINITION_TERM, "Fee"),
        (HTMLBlockType.DEFINITION, "$50"),
        (HTMLBlockType.CODE, "fee = 50"),
        (HTMLBlockType.FIGURE_CAPTION, "Fee table"),
    ]


def test_a_heading_inside_a_link_card_still_starts_a_section() -> None:
    """Tests a heading wrapped in a link (common in card layouts) is still found as a heading."""
    content = parse_html("<main><a href='/fees'><h3>Fees</h3><span>See our fees</span></a></main>")

    assert content.headings == ["Fees"]
    assert [(block.parent_heading, block.text) for block in content.blocks] == [
        ("No heading", "Fees"),
        ("Fees", "See our fees"),
    ]


def test_navigation_and_form_choices_are_not_read() -> None:
    html = "<main><nav>Home About</nav><select><option>Perth</option></select><div>Content</div></main>"

    assert blocks_of(html) == [(HTMLBlockType.TEXT, "Content")]
