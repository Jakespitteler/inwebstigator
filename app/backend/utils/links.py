from urllib.parse import urlparse

DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".zip",
)


def find_added_links(previous_state: list[str], current_state: list[str]) -> list[str]:
    return list(set(current_state) - set(previous_state))


def find_removed_links(previous_state: list[str], current_state: list[str]) -> list[str]:
    return list(set(previous_state) - set(current_state))


def find_link_difference(previous_state: list[str], current_state: list[str]) -> tuple[list[str], list[str]]:
    """Compare found links with stored links and update the database.

    Args:
        previous_state (list[str]): The stored links
        current_state (list[str]): The found links

    Returns:
        tuple[list[str], list[str]]: Added and removed links for the website.
    """
    return find_added_links(previous_state, current_state), find_removed_links(previous_state, current_state)


def is_document(link: str) -> bool:
    return urlparse(link).path.lower().endswith(DOCUMENT_EXTENSIONS)


def separate_document_links(links: list[str]) -> tuple[list[str], list[str]]:
    doc_links: list[str] = []
    non_doc_links: list[str] = []
    for link in links:
        if is_document(link):
            doc_links.append(link)
        else:
            non_doc_links.append(link)
    return doc_links, non_doc_links
