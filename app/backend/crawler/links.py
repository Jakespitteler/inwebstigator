from pathlib import PurePosixPath
from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from bs4.filter import SoupStrainer

from app.core.urls import normalise_url, remove_repeated_pages, site_host

WEB_SCHEMES: frozenset[str] = frozenset({"http", "https"})

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

NON_PAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        *DOCUMENT_EXTENSIONS,
        *(".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".bmp", ".ico", ".tif", ".tiff"),
        *(".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".mov", ".avi", ".wmv", ".webm"),
        *(".rar", ".7z", ".gz", ".tar", ".tgz"),
        *(".css", ".js", ".json", ".xml", ".rss", ".atom", ".ics"),
        *(".woff", ".woff2", ".ttf", ".otf", ".eot"),
        *(".exe", ".msi", ".dmg", ".apk", ".iso", ".epub"),
    }
)


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
    """Determines if a link goes to a web page, rather than a file such as a document, image or stylesheet.

    A path is a page unless it ends in a known file extension, so a page with a dot in its name (e.g.
    "/people/j.smith") is still a page.

    Args:
        parsed_url: The parsed URL structure to check.

    Returns:
        True if the URL's path does not end in a file extension that is not a web page.
    """
    return PurePosixPath(parsed_url.path).suffix.lower() not in NON_PAGE_EXTENSIONS


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

    if site_host(parsed_base_url.netloc) != site_host(parsed_check_url.netloc):
        return False
    if not _is_web_page(parsed_check_url):
        return False

    base_url_path = PurePosixPath(parsed_base_url.path)
    check_url_path = PurePosixPath(parsed_check_url.path)

    # Must match the base path or be a deeper subpath
    return base_url_path == check_url_path or base_url_path in check_url_path.parents


def _base_href(url: str, soup: BeautifulSoup) -> str:
    """Works out the URL a page's relative links are resolved against: its `<base href>` if it has one, otherwise
    the page's own URL.

    Args:
        url: The page's URL, after any redirects.
        soup: The parsed page.

    Returns:
        The URL to resolve relative links against.
    """
    base_tag = soup.find("base", href=True)
    return urljoin(url, str(base_tag["href"]).strip()) if isinstance(base_tag, Tag) else url


def page_link_base(url: str, html_content: str) -> str:
    """Works out the URL a page's relative links are resolved against, from its whole HTML (`<base href>` is in its
    `<head>`), for when the links are then read from only part of the page.

    Args:
        url: The page's URL, after any redirects.
        html_content: The page's whole HTML.

    Returns:
        The page's `<base href>` if it has one, otherwise the page's own URL.
    """
    return _base_href(url, BeautifulSoup(html_content, "html.parser", parse_only=SoupStrainer("base")))


def extract_links_from_html(
    url: str,
    html_content: str,
    internal_only: bool = False,
    base_url: str | None = None,
) -> list[str]:
    """Extracts, resolves, and normalises unique links from HTML content.

    Args:
        url: The URL of the page, used to resolve relative paths, unless the page has a `<base href>`.
        html_content: The raw HTML string to be parsed for links.
        internal_only: If True, filters links to only those sharing the base domain and path scope.
        base_url: Optional override URL string used to evaluate domain boundaries when
            internal_only is enabled. Defaults to `url` if omitted.

    Returns:
        A sorted list of absolute web (http or https) URLs found in the HTML matching criteria, each page only once.
        Links to anything else, such as email addresses ("mailto:", in any case), phone numbers or calendars, are
        left out.

    Raises:
        ValueError: If the url is improperly formatted and cannot be parsed correctly.
    """
    if not base_url:
        base_url = url
    parsed_base: ParseResult = urlparse(url)
    if not parsed_base.netloc:
        raise ValueError(f"Invalid url provided: {url}")

    soup = BeautifulSoup(html_content, "html.parser")
    link_base: str = _base_href(url, soup)
    links: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href: str = str(tag["href"]).strip()

        # Skip empty links and links to a part of the same page
        if not href or href.startswith("#"):
            continue

        # Resolve relative links into absolute URLs, keeping only links to web pages
        absolute_url: str = urljoin(link_base, href)
        if urlparse(absolute_url).scheme not in WEB_SCHEMES:
            continue

        # Filter by internal domain if requested
        if internal_only and not is_internal_web_page(base_url, check_url=absolute_url):
            continue

        links.add(normalise_url(absolute_url))

    return remove_repeated_pages(sorted(links))
