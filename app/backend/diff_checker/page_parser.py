import re
from collections.abc import Callable, Iterator

from bs4 import BeautifulSoup, Comment, Tag
from bs4.element import NavigableString, PageElement, PreformattedString

from app.backend.diff_checker.models import PageContent
from app.models.content_block_models import ContentBlock, HTMLBlockType

HEADING_TAGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
IGNORED_SECTION_TAGS: frozenset[str] = frozenset({"nav", "aside"})
FORM_CONTROL_TAGS: frozenset[str] = frozenset({"select", "option", "optgroup", "datalist", "textarea"})
INLINE_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "br",
        "button",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    }
)
NO_HEADING: str = "No heading"


def normalize_text(text: str) -> str:
    """Removes extra whitespace and newlines from a string.

    Args:
        text: The raw string to clean and normalize.

    Returns:
        A single-line string with all continuous whitespace collapsed into
        single spaces and leading/trailing whitespace removed.
    """
    return " ".join(text.split())


def parse_standard_text(tag: Tag) -> str:
    """Extracts and normalises text from a standard HTML tag element.

    Args:
        tag: The BeautifulSoup Tag object to extract text content from.

    Returns:
        The normalized text content extracted from the tag, with space-separated
        text nodes.
    """
    return normalize_text(tag.get_text(" ", strip=True))


def parse_table_row(tag: Tag) -> str:
    """Extracts text from table header and data cells, joining them with pipe delimiters.

    Args:
        tag: The BeautifulSoup Tag object representing a table row (`<tr>`).

    Returns:
        A pipe-delimited string representing the row contents (e.g., "Col 1 | Col 2").
    """
    cells = tag.find_all(["th", "td"])
    return " | ".join(normalize_text(cell.get_text(" ", strip=True)) for cell in cells)


BLOCK_PARSERS: dict[HTMLBlockType, Callable[[Tag], str]] = {
    HTMLBlockType.PARAGRAPH: parse_standard_text,
    HTMLBlockType.QUOTE: parse_standard_text,
    HTMLBlockType.LIST_ITEM: parse_standard_text,
    HTMLBlockType.CODE: parse_standard_text,
    HTMLBlockType.DEFINITION_TERM: parse_standard_text,
    HTMLBlockType.DEFINITION: parse_standard_text,
    HTMLBlockType.FIGURE_CAPTION: parse_standard_text,
    HTMLBlockType.TABLE_CAPTION: parse_standard_text,
    HTMLBlockType.TABLE_ROW: parse_table_row,
}


def clean_html(soup: BeautifulSoup) -> BeautifulSoup:
    """Removes non-content elements, HTML comments, and embedded raw HTML text nodes from a DOM tree.

    Args:
        soup: The BeautifulSoup DOM tree to sanitize.

    Returns:
        The mutated BeautifulSoup object with unwanted tags, comments, and raw text HTML elements removed.
    """
    unwanted_tags: frozenset[str] = frozenset(["script", "style", "noscript", "template", "svg"])

    for tag in soup(list(unwanted_tags)):
        tag.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for text_node in soup.find_all(string=True):
        # A tag name is only letters, digits and dashes, so text such as `<records@example.gov.au>` is kept.
        if (
            "<" in text_node
            and ">" in text_node
            and re.search(r"<\s*/?\s*[a-zA-Z][a-zA-Z0-9-]*(\s[^>]*)?/?>", text_node)
        ):
            text_node.extract()

    return soup


def extract_last_updated(container: Tag) -> str | None:
    """Locates a 'Last Updated:' heading in the container and extracts its adjacent date text.

    Args:
        container: The root Tag or BeautifulSoup object to search.

    Returns:
        The normalized text from the immediate sibling element of the 'Last Updated:' heading,
        or None if no matching heading or sibling date element is found.
    """
    heading: Tag | None = container.find(
        lambda tag: tag.name in HEADING_TAGS and tag.get_text(" ", strip=True).lower() == "last updated:"
    )
    if not heading:
        return None

    date_sibling: Tag | None = heading.find_next_sibling()
    return parse_standard_text(date_sibling) if date_sibling else None


def _is_inline(tag: Tag) -> bool:
    """Checks whether a tag only styles or links text within a line, e.g. `<strong>` or `<a>`.

    An inline tag wrapping a block (e.g. `<a><h3>Title</h3></a>`, common in card layouts) is treated as a
    container instead, so the block inside it is still found.

    Args:
        tag: The tag to check.

    Returns:
        True if the tag's text belongs to the line of text around it.
    """
    return tag.name in INLINE_TAGS and tag.find(lambda descendant: descendant.name not in INLINE_TAGS) is None


def _text_of(element: PageElement) -> str:
    """Gets the visible text of a piece of a line of text: a text node or an inline tag.

    Args:
        element: The text node or inline tag.

    Returns:
        Its text, or an empty string for a node that is not shown (e.g. a doctype).
    """
    if isinstance(element, Tag):
        return element.get_text(" ")
    if isinstance(element, NavigableString) and not isinstance(element, PreformattedString):
        return str(element)
    return ""


def _pieces_of_tag(tag: Tag, ignore_parents: frozenset[str]) -> Iterator[Tag | str]:
    """Yields the headings, blocks and loose text inside a block-level tag, in page order.

    Args:
        tag: A tag that is not inline.
        ignore_parents: Tag names whose contents are skipped (e.g. `nav`).

    Yields:
        The tag itself if it is a heading or block, otherwise the pieces found inside it.
    """
    if tag.name in ignore_parents or tag.name in FORM_CONTROL_TAGS:
        return
    if tag.name in HEADING_TAGS or tag.name in BLOCK_PARSERS:
        yield tag
        return
    yield from _page_pieces(tag, ignore_parents)


def _page_pieces(container: Tag, ignore_parents: frozenset[str]) -> Iterator[Tag | str]:
    """Yields, in page order, each heading and block tag inside a container, and each run of loose text.

    Loose text is text that sits straight inside a container such as a `<div>` or `<section>`, rather than in a
    paragraph, list or table. Text and inline tags next to each other make one run, so `Fee: <b>$50</b>` is
    read as "Fee: $50".

    Args:
        container: The tag to search.
        ignore_parents: Tag names whose contents are skipped (e.g. `nav`).

    Yields:
        Heading and block tags, and runs of loose text as strings.
    """
    line_parts: list[str] = []
    for child in container.children:
        if isinstance(child, Tag) and not _is_inline(child):
            if loose_text := normalize_text(" ".join(line_parts)):
                yield loose_text
            line_parts = []
            yield from _pieces_of_tag(child, ignore_parents)
        else:
            line_parts.append(_text_of(child))

    if loose_text := normalize_text(" ".join(line_parts)):
        yield loose_text


def _nested_headings(block: Tag) -> list[str]:
    """Finds the text of the headings inside a block, e.g. `<li><h3>Fees</h3>...</li>`.

    Args:
        block: The block tag to search.

    Returns:
        The text of each non-empty heading in the block, in page order.
    """
    return [text for heading in block.find_all(list(HEADING_TAGS)) if (text := parse_standard_text(heading))]


def extract_sequential_blocks(container: Tag, ignore_parents: frozenset[str]) -> tuple[list[str], list[ContentBlock]]:
    """Traverses an HTML element tree to sequentially extract headings and associate content blocks.

    Headings are recorded as content blocks too, in page order, so a renamed heading can be reported once
    as its own change rather than through every block beneath it. A heading inside another block starts a
    new section but is not recorded separately, as the block's text already includes it.

    Args:
        container: The root Tag element to search for structured content.
        ignore_parents: A set of HTML tag names whose descendant elements will be skipped.

    Returns:
        A tuple containing two elements:
            - A list of extracted heading strings.
            - A list of ContentBlock objects (headings included) paired with their most recent preceding
              heading title.
    """
    headings: list[str] = []
    blocks: list[ContentBlock] = []
    current_heading: str = NO_HEADING

    for piece in _page_pieces(container, ignore_parents):
        if isinstance(piece, str):
            blocks.append(ContentBlock(parent_heading=current_heading, block_type=HTMLBlockType.TEXT, text=piece))
            continue

        block_type = HTMLBlockType(piece.name)
        text: str = BLOCK_PARSERS.get(block_type, parse_standard_text)(piece)
        if text:
            blocks.append(ContentBlock(parent_heading=current_heading, block_type=block_type, text=text))

        section_titles: list[str] = [text] if piece.name in HEADING_TAGS and text else _nested_headings(piece)
        headings.extend(section_titles)
        current_heading = section_titles[-1] if section_titles else current_heading

    return headings, blocks


def main_content_html(html: str) -> str:
    """Returns the HTML of the part of a page whose changes are watched: its `<main>` (or `<body>`), without its
    navigation and side panels (`<nav>`, `<aside>`).

    Links are taken from the same part of the page as the text, so a change to a menu shared by every page is not
    reported as a change on every critical page.

    Args:
        html: The page's HTML.

    Returns:
        The HTML of the page's main content.
    """
    soup = BeautifulSoup(html, "html.parser")
    main_container: Tag | BeautifulSoup = soup.find("main") or soup.find("body") or soup
    for ignored_section in main_container.find_all(list(IGNORED_SECTION_TAGS)):
        ignored_section.decompose()
    return str(main_container)


def parse_html(html: str) -> PageContent:
    """Parses a raw HTML string into structured content blocks, headings and metadata.

    Cleans script and noise tags, locates the primary document container (`<main>`,
    `<body>`, or root), excludes non-content regions (`<nav>`, `<aside>`), and aggregates
    parsed contents into a unified PageContent data structure.

    Args:
        html: The raw HTML document string to process.

    Returns:
        A fully populated PageContent object containing structured headings, sequential
        content blocks, and last-updated metadata.
    """
    cleaned_soup: BeautifulSoup = clean_html(BeautifulSoup(html, "html.parser"))
    main_container: Tag | BeautifulSoup = cleaned_soup.find("main") or cleaned_soup.find("body") or cleaned_soup

    headings, blocks = extract_sequential_blocks(main_container, IGNORED_SECTION_TAGS)
    return PageContent(headings=headings, blocks=blocks, last_updated=extract_last_updated(main_container))
