import re
from collections.abc import Callable
from enum import StrEnum

from bs4 import BeautifulSoup, Comment, Tag
from pydantic import BaseModel, Field


def normalize_text(text: str) -> str:
    """Removes extra whitespace and newlines."""
    return " ".join(text.split())


def parse_standard_text(tag: Tag) -> str:
    return normalize_text(tag.get_text(" ", strip=True))


def parse_table_row(tag: Tag) -> str:
    cells = tag.find_all(["th", "td"])
    return " | ".join(normalize_text(cell.get_text(" ", strip=True)) for cell in cells)


class HTMLBlockType(StrEnum):
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
    parent_heading: str | None = None
    block_type: HTMLBlockType
    text: str


class ChangedBlock(BaseModel):
    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float


class PageContent(BaseModel):
    headings: list[str] = Field(default_factory=list[str])
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    links: list[str] = Field(default_factory=list[str])
    last_updated: str | None = None


def clean_html(soup: BeautifulSoup) -> BeautifulSoup:
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
    return [
        str(link["href"])
        for link in container.find_all("a", href=True)
        if not link.find_parent(list(ignore_parents)) and link.get("href")
    ]  # TODO May be able to use the other extract links function


def extract_last_updated(container: Tag) -> str | None:
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
