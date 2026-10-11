import pytest
from bs4 import BeautifulSoup, Tag

from app.backend.diff_checker.content_diff import compare_page_content
from app.backend.diff_checker.models import PageContent
from app.backend.diff_checker.page_parser import (
    clean_html,
    extract_last_updated,
    extract_sequential_blocks,
    main_content_html,
    normalise_text,
    parse_html,
    parse_standard_text,
    parse_table_row,
)
from app.models.content_block_models import HTMLBlockType


def test_normalise_text_basic() -> None:
    raw: str = "  Hello   \n  world!  "
    assert normalise_text(raw) == "Hello world!"


def test_normalise_text_empty() -> None:
    assert normalise_text("   \n\t  ") == ""


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
    """Tests a text node holding raw HTML (escaped markup leaked by a CMS) is removed, and real text is kept."""
    html_content: str = "<div><p>&lt;b&gt;Escaped markup&lt;/b&gt;</p><p>Valid text</p></div>"
    soup: BeautifulSoup = BeautifulSoup(html_content, "html.parser")
    cleaned: BeautifulSoup = clean_html(soup)

    assert "Escaped markup" not in cleaned.get_text()
    assert [paragraph.get_text() for paragraph in cleaned.find_all("p")] == ["", "Valid text"]


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


def link_targets(html: str) -> list[str]:
    """Lists where each link in a piece of HTML goes, in page order."""
    return [str(link["href"]) for link in BeautifulSoup(html, "html.parser").select("a[href]")]


@pytest.mark.parametrize(
    "hidden_html",
    [
        "<script>var fee = 50;</script>",
        "<style>p { color: red; }</style>",
        "<noscript>Please turn on JavaScript</noscript>",
        "<template><p>Hidden fee</p></template>",
        "<svg><text>Chart label</text></svg>",
    ],
)
def test_scripts_styles_templates_and_drawings_are_not_read(hidden_html: str) -> None:
    """Tests text in tags that are never shown as page text is left out of the page's blocks."""
    assert blocks_of(f"<main>{hidden_html}<div>Content</div></main>") == [(HTMLBlockType.TEXT, "Content")]


@pytest.mark.parametrize(
    "ignored_html",
    [
        "<aside>Related pages</aside>",
        "<div><nav>Section menu</nav></div>",
        "<textarea>Type your question here</textarea>",
        "<datalist><option>Perth</option></datalist>",
        "<select><optgroup label='WA'><option>Perth</option></optgroup></select>",
    ],
)
def test_side_panels_nested_menus_and_form_controls_are_not_read(ignored_html: str) -> None:
    """Tests side panels, menus nested deeper in the page and form controls are left out of the page's blocks."""
    assert blocks_of(f"<main>{ignored_html}<p>Content</p></main>") == [(HTMLBlockType.PARAGRAPH, "Content")]


def test_a_doctype_is_not_read_as_text() -> None:
    """Tests the doctype of a page with no <main> or <body>, where the whole page is read, is not read as text."""
    assert blocks_of("<!DOCTYPE html><div>Fee is $50.</div>") == [(HTMLBlockType.TEXT, "Fee is $50.")]


def test_only_the_main_content_is_read() -> None:
    """Tests text outside <main>, such as a site banner or footer, is not read when the page has a <main>."""
    html = (
        "<body><header><p>Site banner</p></header><main><p>Fee is $50.</p></main>"
        "<footer><p>Copyright 2026</p></footer></body>"
    )

    assert blocks_of(html) == [(HTMLBlockType.PARAGRAPH, "Fee is $50.")]


def test_the_body_is_read_when_there_is_no_main() -> None:
    """Tests the <body> is read when a page has no <main>, so the page's <head> is not read as text."""
    html = "<html><head><title>Licence fees</title></head><body><p>Fee is $50.</p></body></html>"

    assert blocks_of(html) == [(HTMLBlockType.PARAGRAPH, "Fee is $50.")]


def test_the_pages_own_header_and_footer_are_not_read_when_there_is_no_main() -> None:
    """Tests a page without a <main> does not have its site header or footer read (e.g. a copyright year that changes
    every January), while the header of an article, which holds the article's own title, is still read."""
    html = (
        "<body><header><p>Site banner</p></header>"
        "<article><header><h2>Fees</h2></header><p>Fee is $50.</p></article>"
        "<footer><p>Copyright 2026</p></footer></body>"
    )

    assert blocks_of(html) == [(HTMLBlockType.HEADING_2, "Fees"), (HTMLBlockType.PARAGRAPH, "Fee is $50.")]


def test_links_in_the_pages_own_header_and_footer_are_not_watched_when_there_is_no_main() -> None:
    """Tests a page without a <main> does not have the links in its site header or footer watched, as they are the
    same on every page."""
    html = '<body><header><a href="/home">Home</a></header><p><a href="/fees">Fees</a></p></body>'

    assert 'href="/home"' not in main_content_html(html)
    assert 'href="/fees"' in main_content_html(html)


def test_tables_are_read_row_by_row_with_cells_joined_by_pipes() -> None:
    """Tests a table's caption is one block and each row is one block, with its cells joined by pipes."""
    html = (
        "<main><table><caption>Licence fees</caption>"
        "<thead><tr><th>Licence</th><th>Fee</th></tr></thead>"
        "<tbody><tr><td>Car</td><td>$50</td></tr></tbody></table></main>"
    )

    assert blocks_of(html) == [
        (HTMLBlockType.TABLE_CAPTION, "Licence fees"),
        (HTMLBlockType.TABLE_ROW, "Licence | Fee"),
        (HTMLBlockType.TABLE_ROW, "Car | $50"),
    ]


def test_each_heading_starts_a_section_whatever_its_level() -> None:
    """Tests each block's section is the nearest heading above it, whether that heading is bigger or smaller."""
    content = parse_html(
        "<main><h1>Licences</h1><h2>Fees</h2><p>$50</p><h3>Late fees</h3><p>$10</p><h2>Hours</h2><p>9 to 5</p></main>"
    )

    assert content.headings == ["Licences", "Fees", "Late fees", "Hours"]
    assert [
        (block.parent_heading, block.text) for block in content.blocks if block.block_type is HTMLBlockType.PARAGRAPH
    ] == [
        ("Fees", "$50"),
        ("Late fees", "$10"),
        ("Hours", "9 to 5"),
    ]


def test_an_empty_heading_does_not_start_a_section() -> None:
    """Tests a heading with no text is not recorded, so the text below it stays in the section above."""
    content = parse_html("<main><h2>Hours</h2><h3> </h3><p>Open 9am to 5pm</p></main>")

    assert content.headings == ["Hours"]
    assert [(block.parent_heading, block.text) for block in content.blocks] == [
        ("No heading", "Hours"),
        ("Hours", "Open 9am to 5pm"),
    ]


def test_the_last_updated_date_is_read_whatever_the_heading_case() -> None:
    """Tests the date next to a "Last updated:" heading is read with its spacing tidied, whatever the case."""
    content = parse_html("<main><h4>LAST UPDATED:</h4><p> 7 October\n  2026 </p><p>Fee is $50.</p></main>")

    assert content.last_updated == "7 October 2026"


def test_a_last_updated_heading_with_nothing_after_it_gives_no_date() -> None:
    """Tests a "Last updated:" heading with no element after it gives no date, rather than failing."""
    soup: BeautifulSoup = BeautifulSoup("<div><p>Fee is $50.</p><h2>Last Updated:</h2></div>", "html.parser")
    container: Tag = soup.find("div")  # type: ignore

    assert extract_last_updated(container) is None


def test_main_content_html_leaves_out_menus_and_side_panels() -> None:
    """Tests the links taken from the main content leave out menus and side panels, inside and outside <main>."""
    html = (
        "<body><nav><a href='/menu'>Menu</a></nav>"
        "<main><p><a href='/fees'>Fees</a></p><aside><a href='/related'>Related</a></aside>"
        "<div><nav><a href='/section-menu'>Section menu</a></nav></div></main></body>"
    )

    assert link_targets(main_content_html(html)) == ["/fees"]


def test_main_content_html_uses_the_body_when_there_is_no_main() -> None:
    """Tests a page with no <main> uses its <body> as the main content, still without menus and side panels."""
    html = (
        "<html><body><nav><a href='/menu'>Menu</a></nav><p><a href='/fees'>Fees</a></p>"
        "<aside><a href='/related'>Related</a></aside></body></html>"
    )

    assert link_targets(main_content_html(html)) == ["/fees"]


def test_text_with_an_email_address_in_angle_brackets_is_read() -> None:
    """Tests a sentence with an email address in angle brackets is read, as it is page text and not raw HTML."""
    html = "<main><p>Send forms to Records &lt;records@example.gov.au&gt; by Friday.</p></main>"

    assert blocks_of(html) == [
        (HTMLBlockType.PARAGRAPH, "Send forms to Records <records@example.gov.au> by Friday."),
    ]
