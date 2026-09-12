import re
from collections.abc import Callable

from bs4 import BeautifulSoup, Comment, Tag

from app.backend.utils.models import ContentBlock, PageContent
from app.backend.utils.text import parse_standard_text, parse_table_row

# Maps HTML tags to their internal block type and the function required to parse them.
BLOCK_PARSERS: dict[str, tuple[str, Callable[[Tag], str]]] = {
    "p": ("paragraph", parse_standard_text),
    "li": ("list_item", parse_standard_text),
    "tr": ("table_row", parse_table_row),
}


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
    ]


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
            block_type, parser_func = BLOCK_PARSERS[tag.name]
            text = parser_func(tag)
            if text:
                blocks.append(ContentBlock(parent_heading=current_heading, block_type=block_type, text=text))

    return headings, blocks


def extract_content(html: str) -> PageContent:
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
