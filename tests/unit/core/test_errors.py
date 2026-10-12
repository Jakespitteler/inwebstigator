import uuid

import pytest

from app.core.errors import (
    NotFoundError,
    PageNotLoadedError,
    ScanAlreadyQueuedError,
    ScanCancelledError,
    ScanError,
    TrafficError,
    WebConnectionError,
    WebCrawlerError,
    WebsiteUnavailableError,
    describe_http_status,
)


def test_not_found_error_names_the_missing_record_by_its_id() -> None:
    """Tests a record looked up by its ID is named by that ID when it is not found."""
    record_id: uuid.UUID = uuid.uuid4()

    error = NotFoundError(id=record_id)

    assert error.id == record_id
    assert str(record_id) in str(error)


def test_not_found_error_names_the_missing_record_by_what_it_was_looked_up_by() -> None:
    """Tests a record looked up by other details (e.g. its URL) is named by those details when it is not found."""
    error = NotFoundError(attributes={"url": "https://example.com/"})

    assert error.attributes == {"url": "https://example.com/"}
    assert "https://example.com/" in str(error)


def test_not_found_error_without_details_still_says_not_found() -> None:
    """Tests a record not found without an ID or other details still gives a readable message."""
    assert str(NotFoundError()) == "not found."


def test_a_website_whose_home_page_cannot_load_counts_as_unreachable() -> None:
    """Tests a website whose home page cannot be loaded is treated like a connection failure (unreachable), and its
    message names the home page."""
    error = WebsiteUnavailableError("https://example.com/")

    assert isinstance(error, WebConnectionError)
    assert error.url == "https://example.com/"
    assert str(error) == "The home page https://example.com/ could not be loaded, so the website could not be scanned."


@pytest.mark.parametrize("error_type", [ScanAlreadyQueuedError, ScanCancelledError])
def test_scan_queue_errors_are_not_crawler_errors(error_type: type[ScanError]) -> None:
    """Tests a scan that is already queued or was cancelled is not mistaken for a website that could not be crawled,
    e.g. by code that turns crawler errors into "could not be loaded"."""
    error = error_type("https://example.com/")

    assert isinstance(error, ScanError)
    assert not isinstance(error, WebCrawlerError)


def test_a_website_whose_home_page_cannot_load_says_why_when_it_is_known() -> None:
    """Tests the reason the home page could not be loaded is kept and added to the message."""
    error = WebsiteUnavailableError("https://example.com/", "HTTP 403 Forbidden")

    assert error.reason == "HTTP 403 Forbidden"
    assert str(error) == (
        "The home page https://example.com/ could not be loaded, so the website could not be scanned. "
        "HTTP 403 Forbidden."
    )


@pytest.mark.parametrize(
    ("status_code", "description"),
    [
        (403, "HTTP 403 Forbidden, so the website refused access and may be blocking the scanner"),
        (404, "HTTP 404 Not Found, so the page does not exist or has moved"),
        (429, "HTTP 429 Too Many Requests, so the website is limiting how often it can be loaded"),
        (503, "HTTP 503 Service Unavailable, so the website may be down, overloaded or under maintenance"),
        (507, "HTTP 507 Insufficient Storage, so the website had an error"),  # Any other server error
        (418, "HTTP 418 I'm a Teapot"),  # A status with no explanation is only named
        (599, "HTTP 599, so the website had an error"),  # A server error with no name
    ],
)
def test_an_error_status_is_named_and_explained(status_code: int, description: str) -> None:
    """Tests an error status from a website is named, and the common ones explained, so the user knows what it means."""
    assert describe_http_status(status_code) == description


def test_a_traffic_error_says_which_page_answered_what() -> None:
    """Tests a rate limited page is named in the error, with the status it kept answering and what that means."""
    error = TrafficError("https://example.com/news", 429)

    assert str(error) == (
        "https://example.com/news kept answering HTTP 429 Too Many Requests, "
        "so the website is limiting how often it can be loaded."
    )


def test_a_traffic_error_with_its_own_message_uses_it() -> None:
    """Tests a traffic error that says more than one page's status (e.g. many pages blocked at once) says that."""
    assert str(TrafficError("https://example.com/", 403, message="50 pages were blocked.")) == "50 pages were blocked."


def test_a_connection_error_says_why_the_website_could_not_be_reached() -> None:
    """Tests a connection error names the website and why it could not be reached, or that it failed or timed out
    when the reason is not known."""
    assert str(WebConnectionError("https://example.com/", "The website refused the connection")) == (
        "https://example.com/ could not be reached. The website refused the connection."
    )
    assert str(WebConnectionError("https://example.com/")) == (
        "https://example.com/ could not be reached. The connection failed or timed out."
    )


def test_a_page_that_cannot_be_added_says_why_when_it_is_known() -> None:
    """Tests a page that cannot be added says why, or asks to check the URL when the reason is not known."""
    assert str(PageNotLoadedError("https://example.com/news", "HTTP 404 Not Found")) == (
        "https://example.com/news could not be loaded. HTTP 404 Not Found."
    )
    assert str(PageNotLoadedError("https://example.com/news")) == (
        "https://example.com/news could not be loaded. Check it exists and the URL is correct."
    )
