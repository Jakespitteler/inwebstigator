from bs4 import BeautifulSoup, Tag

from app.backend.utils.text import (
    normalize_text,
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
