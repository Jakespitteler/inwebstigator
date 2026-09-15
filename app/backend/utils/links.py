import logging
from pathlib import PurePosixPath
from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup

logging.getLogger("httpx2").setLevel(logging.WARNING)

WEB_PAGE_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp"}

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


def _is_web_page(parsed_url: ParseResult) -> bool:
    """Determines if a link goes to a web page (rather than a document).

    Args:
        parsed_url (ParseResult): The parsed_url to check.

    Returns:
        bool: True if the parsed_url goes to a webpage.
    """
    return PurePosixPath(parsed_url.path).suffix.lower() in WEB_PAGE_EXTENSIONS


def is_internal_web_page(base_url: str, check_url: str) -> bool:
    parsed_base_url: ParseResult = urlparse(base_url)
    parsed_check_url: ParseResult = urlparse(check_url)

    if parsed_base_url.netloc != parsed_check_url.netloc:
        return False
    if not _is_web_page(parsed_check_url):
        return False

    base_url_path = PurePosixPath(parsed_base_url.path)
    check_url_path = PurePosixPath(parsed_check_url.path)

    # Must match the base path or be a deeper subpath
    return base_url_path == check_url_path or base_url_path in check_url_path.parents


def normalise_url(url: str) -> str:
    """Normalises a URL by removing fragments and trailing slashes for deduplication.

    Args:
        url (str): The url to normalise.

    Returns:
        str: The normalised url.
    """
    parsed: ParseResult = urlparse(url)
    path: str = parsed.path
    path = "/" if not path or path == "/" else path.rstrip("/")
    return parsed._replace(fragment="", path=path).geturl()


def extract_links_from_html(  # TODO may need to ignore<nav> tags since a link could get added to every page in the website if thats the case (but also we are just checking th ecritical pages so idk)
    url: str,
    html_content: str,
    internal_only: bool = False,
    base_url: str | None = None,
) -> list[str]:
    """Extracts, resolves, and normalises unique links from HTML content.

    Args:
        url (str): The URL of the page, used to resolve relative paths.
        html_content (str): The raw HTML string to be parsed for links.
        internal_only (bool): If True, filters links to only those sharing the base domain.

    Returns:
        list[str]: A sorted list of unique, absolute URLs found in the HTML matching criteria.

    Raises:
        ValueError: If the url is improperly formatted and cannot be parsed correctly.
        TypeError: If an href attribute is not a string.
    """
    if not base_url:
        base_url = url
    parsed_base: ParseResult = urlparse(url)
    if not parsed_base.netloc:
        raise ValueError(f"Invalid url provided: {url}")

    links: set[str] = set()
    for tag in BeautifulSoup(html_content, "html.parser").find_all("a", href=True):
        href: str = str(tag["href"]).strip()

        # Skip non-navigational or empty links
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Resolve relative links into absolute URLs
        absolute_url: str = urljoin(url, href)

        # Filter by internal domain if requested
        if internal_only and not is_internal_web_page(base_url, check_url=absolute_url):
            continue  # TODO only get child URLs

        links.add(normalise_url(absolute_url))

    return sorted(links)
