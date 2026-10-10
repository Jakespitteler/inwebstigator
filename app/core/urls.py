"""Helpers for working with URLs as text: completing a URL typed by the user, and telling when two URLs are the
same page.

They do not fetch anything, so the models, the database services and the crawler can all use them.
"""

from collections.abc import Iterable
from functools import lru_cache
from pathlib import PurePosixPath
from urllib.parse import ParseResult, parse_qsl, urlencode, urljoin, urlparse

DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
TRACKING_PARAMETER_PREFIXES: tuple[str, ...] = ("utm_",)
TRACKING_PARAMETERS: frozenset[str] = frozenset({"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga"})
PAGE_KEY_CACHE_SIZE: int = 100_000


def site_host(netloc: str) -> str:
    """Returns the part of a network location that identifies the website.

    Host names are case-insensitive, and a leading "www." is dropped because sites commonly
    redirect between example.com and www.example.com and link to both.

    Args:
        netloc: The network location of a parsed URL, e.g. "www.example.com".

    Returns:
        The lower-cased network location without a leading "www.".
    """
    return netloc.lower().removeprefix("www.")


def add_missing_scheme(url: str) -> str:
    """Adds "https://" to the front of a URL typed without it, e.g. "example.com" becomes "https://example.com".

    Args:
        url: The URL as the user typed it.

    Returns:
        The URL starting with "https://", or unchanged if it already starts with a scheme such as "http://".
    """
    parsed: ParseResult = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    # A URL starting with "//" (e.g. "//example.com") has its host but no scheme
    return f"https://{url.removeprefix('//')}"


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

    if site_host(urlparse(full_page_url).netloc) != site_host(urlparse(website_url).netloc):
        raise ValueError(f"{page_url} is not a page on {website_url}.")

    return full_page_url


def _without_default_port(parsed: ParseResult) -> str:
    """Returns a URL's lower-cased network location, without its port if it is the scheme's default.

    "https://example.com:443/" and "https://example.com/" are the same page.

    Args:
        parsed: The parsed URL.

    Returns:
        The network location, e.g. "example.com" or "example.com:8080".
    """
    netloc: str = parsed.netloc.lower()
    default_port: int | None = DEFAULT_PORTS.get(parsed.scheme.lower())
    return netloc.removesuffix(f":{default_port}") if default_port else netloc


def _is_tracking_parameter(name: str) -> bool:
    """Checks whether a query parameter only tracks where a visitor came from (e.g. "utm_source"), so it does not
    change the page.

    Args:
        name: The parameter's name.

    Returns:
        True if it is a tracking parameter.
    """
    lower_name: str = name.lower()
    return lower_name in TRACKING_PARAMETERS or lower_name.startswith(TRACKING_PARAMETER_PREFIXES)


def _without_tracking_parameters(query: str) -> str:
    """Removes tracking parameters from a URL's query, keeping the others in their order.

    Args:
        query: The query, e.g. "page=2&utm_source=newsletter".

    Returns:
        The query without tracking parameters, e.g. "page=2". A query without any is returned exactly as it was.
    """
    parameters: list[tuple[str, str]] = parse_qsl(query, keep_blank_values=True)
    kept: list[tuple[str, str]] = [(name, value) for name, value in parameters if not _is_tracking_parameter(name)]
    return query if len(kept) == len(parameters) else urlencode(kept)


def normalise_url(url: str) -> str:
    """Normalises a URL for deduplication: lower-cases its domain, and removes a default port, tracking parameters,
    the fragment and a trailing slash.

    Args:
        url: The url to normalise.

    Returns:
        The normalised url.
    """
    parsed: ParseResult = urlparse(url)
    return _rebuild_url(parsed, netloc=_without_default_port(parsed))


def _rebuild_url(parsed: ParseResult, netloc: str) -> str:
    """Rebuilds a parsed URL with the given network location, without its fragment, tracking parameters or a
    trailing slash.

    Args:
        parsed: The parsed URL.
        netloc: The network location to give the URL, e.g. "example.com".

    Returns:
        The rebuilt URL, keeping "/" as the path of a website's home page.
    """
    path: str = "/" if not parsed.path or parsed.path == "/" else parsed.path.rstrip("/")
    query: str = _without_tracking_parameters(parsed.query)
    return parsed._replace(netloc=netloc, fragment="", path=path, query=query).geturl()


@lru_cache(maxsize=PAGE_KEY_CACHE_SIZE)
def page_key(url: str) -> str:
    """Returns what identifies the page a URL goes to, so URLs written differently for the same page match.

    The same page can be written with http or https, with or without a trailing slash, fragment, leading "www.",
    default port or tracking parameters, and with capitals in the domain. This is only for comparing URLs: a URL is
    still fetched as it was written, as some websites only answer on one of example.com and www.example.com.

    It is the app's one rule for when two URLs are the same page, used for websites, critical pages and crawled pages.

    The keys are cached, as the crawler works out the key of every link on every page, and most of those are the
    same menu links again. Working them all out afresh held up the dashboard while a large website was crawled.

    Args:
        url: A full URL.

    Returns:
        The URL without those differences, e.g. "http://www.Example.com/news/" becomes "https://example.com/news".
    """
    parsed: ParseResult = urlparse(url)
    netloc: str = site_host(_without_default_port(parsed))
    # Websites serve the same pages over http and https (usually redirecting one to the other)
    scheme: str = "https" if parsed.scheme == "http" else parsed.scheme
    return _rebuild_url(parsed._replace(scheme=scheme), netloc=netloc)


def is_same_page(url: str, other_url: str) -> bool:
    """Checks whether two URLs are the same page, e.g. "https://example.com/news" and "http://www.Example.com/news/".

    Args:
        url: A full URL.
        other_url: Another full URL.

    Returns:
        True if the URLs only differ in ways that do not change the page (see `page_key`), otherwise False.
    """
    return page_key(url) == page_key(other_url)


def _is_http_version_of(kept_url: str, url: str) -> bool:
    """Checks whether a URL kept for a page is its http address, and another URL is its https address.

    Args:
        kept_url: The URL kept for the page so far.
        url: Another URL for the same page.

    Returns:
        True if the https address should be kept instead.
    """
    return urlparse(kept_url).scheme == "http" and urlparse(url).scheme == "https"


def remove_repeated_pages(urls: Iterable[str]) -> list[str]:
    """Removes URLs that are the same page as an earlier URL, keeping the first as it was written, except that a
    page's https address is kept over its http address.

    For example "https://www.example.com/news/" is removed when it follows "https://example.com/news", and
    "http://example.com/news" is replaced by "https://example.com/news" when that follows it.

    Args:
        urls: Full URLs.

    Returns:
        The URLs in the order each page first appeared, with each page only once.
    """
    url_by_page: dict[str, str] = {}
    for url in urls:
        key: str = page_key(url)
        if key not in url_by_page or _is_http_version_of(url_by_page[key], url):
            url_by_page[key] = url
    return list(url_by_page.values())
