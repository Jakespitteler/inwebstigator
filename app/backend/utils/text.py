from bs4 import Tag


def normalize_text(text: str) -> str:
    """Removes extra whitespace and newlines."""
    return " ".join(text.split())


def parse_standard_text(tag: Tag) -> str:
    return normalize_text(tag.get_text(" ", strip=True))


def parse_table_row(tag: Tag) -> str:
    cells = tag.find_all(["th", "td"])
    return " | ".join(normalize_text(cell.get_text(" ", strip=True)) for cell in cells)
