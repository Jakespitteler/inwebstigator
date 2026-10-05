import logging
from pathlib import PurePosixPath
from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup

logging.getLogger("httpx2").setLevel(logging.WARNING)

WEB_PAGE_EXTENSIONS: tuple[str, ...] = ("", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp")

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
    """Identifies links that were added between a previous state and a current state.

    Args:
        previous_state: A list of URL strings representing the initial state.
        current_state: A list of URL strings representing the updated state.

    Returns:
        A list of URL strings present in current_state but missing from previous_state.
    """
    return list(set(current_state) - set(previous_state))


def find_removed_links(previous_state: list[str], current_state: list[str]) -> list[str]:
    """Identifies links that were removed between a previous state and a current state.

    Args:
        previous_state: A list of URL strings representing the initial state.
        current_state: A list of URL strings representing the updated state.

    Returns:
        A list of URL strings present in previous_state but missing from current_state.
    """
    return list(set(previous_state) - set(current_state))


def is_document(link: str) -> bool:
    """Determines whether a given URL points to a document file based on its file extension.

    Args:
        link: The target URL string to inspect.

    Returns:
        True if the URL path ends with a supported document extension, False otherwise.
    """
    return urlparse(link).path.lower().endswith(DOCUMENT_EXTENSIONS)


def separate_document_links(links: list[str]) -> tuple[list[str], list[str]]:
    """Categorises a list of URLs into document links and non-document links.

    Args:
        links: A list of URL strings to split into separate lists.

    Returns:
        A tuple containing two lists:
            - The first list contains URLs identified as documents.
            - The second list contains remaining non-document URLs.
    """
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
        parsed_url: The parsed URL structure to check.

    Returns:
        True if the parsed URL path matches recognised web page extensions.
    """
    return PurePosixPath(parsed_url.path).suffix.lower() in WEB_PAGE_EXTENSIONS


def _site_host(netloc: str) -> str:
    """Returns the part of a network location that identifies the website.

    Host names are case-insensitive, and a leading "www." is dropped because sites commonly
    redirect between example.com and www.example.com and link to both.

    Args:
        netloc: The network location of a parsed URL, e.g. "www.example.com".

    Returns:
        The lower-cased network location without a leading "www.".
    """
    return netloc.lower().removeprefix("www.")


def is_internal_web_page(base_url: str, check_url: str) -> bool:
    """Checks whether a URL is an internal webpage residing within the base URL hierarchy.

    Verifies that the target URL is on the same website as the base URL (treating example.com and
    www.example.com as the same), represents a standard web page, and resides at or beneath the
    path level of the base URL.

    Args:
        base_url: The reference base URL string defining the root domain and path scope.
        check_url: The target URL string to validate.

    Returns:
        True if check_url is an internal webpage within the base URL subpath, False otherwise.
    """
    parsed_base_url: ParseResult = urlparse(base_url)
    parsed_check_url: ParseResult = urlparse(check_url)

    if _site_host(parsed_base_url.netloc) != _site_host(parsed_check_url.netloc):
        return False
    if not _is_web_page(parsed_check_url):
        return False

    base_url_path = PurePosixPath(parsed_base_url.path)
    check_url_path = PurePosixPath(parsed_check_url.path)

    # Must match the base path or be a deeper subpath
    return base_url_path == check_url_path or base_url_path in check_url_path.parents


def add_missing_scheme(url: str) -> str:
    """Adds "https://" to the front of a URL typed without it, e.g. "example.com" becomes "https://example.com".

    Args:
        url: The URL as the user typed it.

    Returns:
        The URL starting with "https://", or unchanged if it already starts with a scheme such as "http://".
    """
    return url if urlparse(url).netloc else f"https://{url}"


def _join_onto_website(website_url: str, relative_link: str) -> str:
    """Joins a link onto the website's URL, adding the website's path if the link does not already include it.

    For the website "https://example.com/au", both "/news" and "/au/news" become "https://example.com/au/news".

    Args:
        website_url: The full URL of the website.
        relative_link: The link relative to the website, starting with "/".

    Returns:
        The full URL of the link.
    """
    website_path: PurePosixPath = PurePosixPath(urlparse(website_url).path or "/")
    link_path: PurePosixPath = PurePosixPath(urlparse(relative_link).path)

    # The link already includes the website's path, so it only needs the website's host
    if link_path == website_path or website_path in link_path.parents:
        return urljoin(website_url, relative_link)

    return urljoin(website_url, str(website_path).rstrip("/") + relative_link)


def resolve_critical_page_url(website_url: str, page_url: str) -> str:
    """Turns a critical page typed by the user into a full URL on the website.

    The page can be a full URL ("https://example.com/news"), a URL without "https://" ("example.com/news")
    or a link relative to the website ("/news").

    Args:
        website_url: The full URL of the website the critical page belongs to.
        page_url: The critical page as the user typed it.

    Returns:
        The full URL of the critical page.

    Raises:
        ValueError: If the critical page is on a different website.
    """
    # Links starting with a single "/" are relative to the website ("//" would start a link to another website)
    is_relative_link: bool = page_url.startswith("/") and not page_url.startswith("//")
    full_page_url: str = _join_onto_website(website_url, page_url) if is_relative_link else add_missing_scheme(page_url)

    if _site_host(urlparse(full_page_url).netloc) != _site_host(urlparse(website_url).netloc):
        raise ValueError(f"{page_url} is not a page on {website_url}.")

    return full_page_url


def normalise_url(url: str) -> str:
    """Normalises a URL by removing fragments and trailing slashes for deduplication.

    Args:
        url: The url to normalise.

    Returns:
        The normalised url.
    """
    parsed: ParseResult = urlparse(url)
    path: str = parsed.path
    path = "/" if not path or path == "/" else path.rstrip("/")
    return parsed._replace(fragment="", path=path).geturl()


# TODO may need to ignore <nav> tags since a link could get added to every page in the website if that's the case
# (but also we are just checking th critical pages so idk)
def extract_links_from_html(
    url: str,
    html_content: str,
    internal_only: bool = False,
    base_url: str | None = None,
) -> list[str]:
    """Extracts, resolves, and normalises unique links from HTML content.

    Args:
        url: The URL of the page, used to resolve relative paths.
        html_content: The raw HTML string to be parsed for links.
        internal_only: If True, filters links to only those sharing the base domain and path scope.
        base_url: Optional override URL string used to evaluate domain boundaries when
            internal_only is enabled. Defaults to `url` if omitted.

    Returns:
        A sorted list of unique, absolute URLs found in the HTML matching criteria.

    Raises:
        ValueError: If the url is improperly formatted and cannot be parsed correctly.
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
            continue

        links.add(normalise_url(absolute_url))

    return sorted(links)
