import pytest

from app.backend.utils.links import (
    find_added_links,
    find_link_difference,
    find_removed_links,
    is_document,
    separate_document_links,
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


def test_find_link_difference() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page2", "/page3"]
    added, removed = find_link_difference(previous, current)
    assert added == ["/page3"]
    assert removed == ["/page1"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/document.pdf",
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
        "https://example.com/page.html",
        "http://example.com/pdf-viewer",
        "/about-us",
        "https://example.com/document.pdf.html",
    ],
)
def test_is_document_false(url: str) -> None:
    assert is_document(url) is False


def test_separate_document_links() -> None:
    links: list[str] = [
        "https://example.com/index.html",
        "https://example.com/resume.pdf",
        "/contact",
        "/docs/manual.docx",
    ]
    docs, non_docs = separate_document_links(links)

    assert set(docs) == {"https://example.com/resume.pdf", "/docs/manual.docx"}
    assert set(non_docs) == {"https://example.com/index.html", "/contact"}
