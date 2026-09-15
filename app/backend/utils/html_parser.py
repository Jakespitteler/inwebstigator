import re
from collections.abc import Callable
from enum import StrEnum

from bs4 import BeautifulSoup, Comment, Tag
from pydantic import BaseModel, Field


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


class HTMLBlockType(StrEnum):
    """Enumeration of supported HTML block element tag names."""

    PARAGRAPH = "p"
    HEADING_1 = "h1"
    HEADING_2 = "h2"
    HEADING_3 = "h3"
    HEADING_4 = "h4"
    HEADING_5 = "h5"
    HEADING_6 = "h6"
    CODE = "pre"
    QUOTE = "blockquote"
    LIST = "blockquote"
    UNORDERED_LIST = "ul"
    ORDERED_LIST = "ol"
    DIVISION = "div"
    TABLE_ROW = "tr"


# Maps HTML tags to their internal block type and the function required to parse them.
BLOCK_PARSERS: dict[HTMLBlockType, Callable[[Tag], str]] = {
    HTMLBlockType.PARAGRAPH: parse_standard_text,
    HTMLBlockType.LIST: parse_standard_text,
    HTMLBlockType.TABLE_ROW: parse_table_row,
}


class ContentBlock(BaseModel):
    """Represents a structured block of parsed HTML text contextually tied to its parent heading.

    Attributes:
        parent_heading: The most recent section heading preceding this content block,
            or None if no preceding heading exists.
        block_type: The structural HTML block type classification for this content.
        text: The normalized text content contained within the block.
    """

    parent_heading: str | None = None
    block_type: HTMLBlockType
    text: str


class ChangedBlock(BaseModel):
    """Represents the difference and similarity comparison between two content blocks.

    Attributes:
        old_block: The original content block state before modification.
        new_block: The updated content block state after modification.
        similarity: A floating-point score ranging from 0.0 to 1.0 indicating
            the similarity between the two blocks.
    """

    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float


class PageContent(BaseModel):
    """Container model representing the complete parsed content and metadata of an HTML page.

    Attributes:
        headings: A list of all section titles extracted sequentially from the document.
        blocks: A list of all structured content blocks extracted sequentially.
        links: A list of extracted hyperlink URLs found within the main document container.
        last_updated: A string representation of the page's last update timestamp/date,
            if extracted, otherwise None.
    """

    headings: list[str] = Field(default_factory=list[str])
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    links: list[str] = Field(default_factory=list[str])
    last_updated: str | None = None


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
        if "<" in text_node and ">" in text_node and re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", text_node):
            text_node.extract()

    return soup


def extract_links(container: Tag, ignore_parents: frozenset[str]) -> list[str]:
    """Extracts all valid hyperlink URLs from a container tag, excluding specified parent wrappers.

    Args:
        container: The root Tag or BeautifulSoup object to search for anchor tags.
        ignore_parents: A set of HTML tag names (e.g., 'nav', 'aside') whose children
            should be ignored during link extraction.

    Returns:
        A list of non-empty `href` attribute URL strings extracted from valid anchor tags.
    """
    return [
        str(link["href"])
        for link in container.find_all("a", href=True)
        if not link.find_parent(list(ignore_parents)) and link.get("href")
    ]  # TODO May be able to use the other extract links function


def extract_last_updated(container: Tag) -> str | None:
    """Locates a 'Last Updated:' heading in the container and extracts its adjacent date text.

    Args:
        container: The root Tag or BeautifulSoup object to search.

    Returns:
        The normalized text from the immediate sibling element of the 'Last Updated:' heading,
        or None if no matching heading or sibling date element is found.
    """
    heading: Tag | None = container.find(
        lambda tag: (
            tag.name in ["h1", "h2", "h3", "h4", "h5", "h6"]
            and tag.get_text(" ", strip=True).lower() == "last updated:"
        )
    )
    if not heading:
        return None

    date_sibling: Tag | None = heading.find_next_sibling()
    return parse_standard_text(date_sibling) if date_sibling else None


def extract_sequential_blocks(container: Tag, ignore_parents: frozenset[str]) -> tuple[list[str], list[ContentBlock]]:
    """Traverses an HTML element tree to sequentially extract headings and associate content blocks.

    Args:
        container: The root Tag element to search for structured content.
        ignore_parents: A set of HTML tag names whose descendant elements will be skipped.

    Returns:
        A tuple containing two elements:
            - A list of extracted heading strings.
            - A list of ContentBlock objects paired with their most recent preceding heading title.
    """
    heading_tags: frozenset[str] = frozenset(["h1", "h2", "h3", "h4", "h5", "h6"])
    search_tags: frozenset[str] = heading_tags | BLOCK_PARSERS.keys()

    headings: list[str] = []
    blocks: list[ContentBlock] = []
    current_heading = "No heading"

    for tag in container.find_all(list(search_tags)):
        if tag.find_parent(list(ignore_parents)):
            continue

        if tag.name in heading_tags:
            text = parse_standard_text(tag)
            if text:
                headings.append(text)
                current_heading = text

        elif tag.name in BLOCK_PARSERS:
            block_type = HTMLBlockType(tag.name)
            parser_func = BLOCK_PARSERS[block_type]
            text = parser_func(tag)
            if text:
                blocks.append(ContentBlock(parent_heading=current_heading, block_type=block_type, text=text))

    return headings, blocks


def parse_html(html: str) -> PageContent:
    """Parses a raw HTML string into structured content blocks, headings, links, and metadata.

    Cleans script and noise tags, locates the primary document container (`<main>`,
    `<body>`, or root), excludes non-content regions (`<nav>`, `<aside>`), and aggregates
    parsed contents into a unified PageContent data structure.

    Args:
        html: The raw HTML document string to process.

    Returns:
        A fully populated PageContent object containing structured headings, sequential
        content blocks, hyperlinked URLs, and last-updated metadata.
    """
    cleaned_soup: BeautifulSoup = clean_html(BeautifulSoup(html, "html.parser"))

    main_container: Tag | BeautifulSoup = cleaned_soup.find("main") or cleaned_soup.find("body") or cleaned_soup
    ignore_parents: frozenset[str] = frozenset(["nav", "aside"])

    headings, blocks = extract_sequential_blocks(main_container, ignore_parents)

    return PageContent(
        headings=headings,
        blocks=blocks,
        links=extract_links(main_container, ignore_parents),
        last_updated=extract_last_updated(main_container),
    )
