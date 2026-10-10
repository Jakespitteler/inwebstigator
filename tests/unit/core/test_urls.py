import pytest

from app.core.urls import (
    add_missing_scheme,
    is_same_page,
    normalise_url,
    page_key,
    remove_repeated_pages,
    resolve_critical_page_url,
)


@pytest.mark.parametrize(
    ("url", "other_url", "expected"),
    [
        ("https://example.com", "https://example.com/", True),
        ("https://example.com/news", "https://example.com/news/", True),
        ("https://example.com/news#latest", "https://example.com/news", True),
        ("https://www.example.com/news", "https://example.com/news", True),
        ("https://Example.COM/news", "https://example.com/news", True),
        ("https://WWW.Example.com/news/", "https://example.com/news", True),
        ("http://example.com/news", "https://example.com/news", True),
        ("https://example.com/News", "https://example.com/news", False),
        ("https://example.com/news", "https://example.com/sport", False),
        ("https://example.com/news?page=2", "https://example.com/news", False),
        ("https://news.example.com/", "https://example.com/", False),
    ],
)
def test_is_same_page_ignores_differences_that_do_not_change_the_page(url: str, other_url: str, expected: bool) -> None:
    """Test URLs that only differ by a trailing slash, fragment, leading "www.", capitals in the domain or http
    instead of https are the same page, while a different path (including its capitals), query or subdomain is a
    different page."""
    assert is_same_page(url, other_url) is expected


def test_remove_repeated_pages_keeps_the_first_of_each_page() -> None:
    """Test a page written a second time, e.g. with a trailing slash, is removed, keeping the order and the first
    way it was written."""
    urls = [
        "https://example.com",
        "https://example.com/news/",
        "https://www.example.com/",
        "https://Example.com/news",
    ]

    assert remove_repeated_pages(urls) == ["https://example.com", "https://example.com/news/"]


def test_remove_repeated_pages_keeps_a_pages_https_address_over_its_http_one() -> None:
    """Test a page written with http and then https is kept once, at its https address, in the place it first
    appeared."""
    urls = ["http://example.com/news", "https://example.com/", "https://example.com/news/"]

    assert remove_repeated_pages(urls) == ["https://example.com/news/", "https://example.com/"]


def test_page_key_only_removes_differences_that_do_not_change_the_page() -> None:
    """Test the page key drops a leading "www.", capitals in the domain, a trailing slash and a fragment, and writes
    http as https, but keeps the path's capitals and the query, which can change the page."""
    assert page_key("https://WWW.Example.com/News/?id=1#top") == "https://example.com/News?id=1"
    assert page_key("https://www.example.com") == "https://example.com/"
    assert page_key("http://example.com:80/news") == "https://example.com/news"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com:443/news/", "https://example.com/news"),
        ("http://example.com:80/news", "http://example.com/news"),
        ("https://example.com:8443/news", "https://example.com:8443/news"),
        ("https://example.com/news?utm_source=newsletter&utm_medium=email", "https://example.com/news"),
        ("https://example.com/news?page=2&fbclid=abc&UTM_Campaign=x", "https://example.com/news?page=2"),
        ("https://example.com/search?q=a+b&page=2", "https://example.com/search?q=a+b&page=2"),
    ],
    ids=["https-default-port", "http-default-port", "other-port-kept", "utm", "mixed-tracking", "query-unchanged"],
)
def test_normalise_url_removes_default_ports_and_tracking_parameters(url: str, expected: str) -> None:
    """Tests the same page written with its default port or with tracking parameters is stored once."""
    assert normalise_url(url) == expected


def test_page_key_treats_default_ports_and_tracking_parameters_as_the_same_page() -> None:
    """Tests links that only differ by a default port, tracking parameters or "www." are compared as one page."""
    assert is_same_page("https://www.example.com:443/news?utm_source=x", "https://example.com/news")


@pytest.mark.parametrize(
    ("typed_url", "expected"),
    [
        ("example.com", "https://example.com"),
        ("www.example.com/news", "https://www.example.com/news"),
        ("example.com:8080/news", "https://example.com:8080/news"),
        ("//example.com/news", "https://example.com/news"),
        ("https://example.com/news", "https://example.com/news"),
        ("http://example.com", "http://example.com"),
    ],
    ids=["host-only", "with-path", "with-port", "starts-with-slashes", "https-kept", "http-kept"],
)
def test_add_missing_scheme_completes_a_url_typed_without_one(typed_url: str, expected: str) -> None:
    """Tests "https://" is added to a URL typed without a scheme, and a URL that has one is left as it is."""
    assert add_missing_scheme(typed_url) == expected


@pytest.mark.parametrize(
    ("website_url", "page_url", "expected"),
    [
        ("https://example.com", "https://example.com/news", "https://example.com/news"),
        ("https://example.com", "https://www.example.com/news", "https://www.example.com/news"),
        ("https://example.com", "example.com/news", "https://example.com/news"),
        ("https://example.com", "http://example.com/news", "http://example.com/news"),
        ("https://example.com", "//example.com/news", "https://example.com/news"),
        ("https://example.com", "/news", "https://example.com/news"),
        ("https://example.com/", "/news?page=2", "https://example.com/news?page=2"),
        ("https://example.com/au", "/news", "https://example.com/au/news"),
        ("https://example.com/au/", "/news", "https://example.com/au/news"),
        ("https://example.com/au", "/au/news", "https://example.com/au/news"),
        ("https://example.com/au", "/au", "https://example.com/au"),
    ],
    ids=[
        "full-url",
        "full-url-with-www",
        "without-https",
        "http-kept",
        "starts-with-slashes",
        "relative",
        "relative-with-query",
        "relative-to-a-website-with-a-path",
        "relative-to-a-website-with-a-path-and-slash",
        "relative-already-including-the-path",
        "the-website-path-itself",
    ],
)
def test_resolve_critical_page_url_turns_a_typed_page_into_a_full_url(
    website_url: str, page_url: str, expected: str
) -> None:
    """Tests a critical page typed as a full URL, without "https://" or relative to the website (including a website
    with a path, e.g. example.com/au) becomes the page's full URL on the website."""
    assert resolve_critical_page_url(website_url, page_url) == expected


@pytest.mark.parametrize(
    "page_url",
    ["https://other.com/news", "other.com/news", "//other.com/news", "https://news.example.com/latest"],
    ids=["full-url", "without-https", "starts-with-slashes", "subdomain"],
)
def test_resolve_critical_page_url_refuses_a_page_on_another_website(page_url: str) -> None:
    """Tests a critical page on a different website (or subdomain) is refused, rather than watched."""
    with pytest.raises(ValueError, match="is not a page on https://example.com"):
        resolve_critical_page_url("https://example.com", page_url)
