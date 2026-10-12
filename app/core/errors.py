import uuid
from http import HTTPStatus
from typing import Any

# What a status code means for the website, for the ones a scan commonly gets back
HTTP_STATUS_EXPLANATIONS: dict[int, str] = {
    401: "the page needs a login",
    403: "the website refused access and may be blocking the scanner",
    404: "the page does not exist or has moved",
    410: "the page has been removed",
    429: "the website is limiting how often it can be loaded",
    500: "the website had an error",
    502: "the website may be down, overloaded or under maintenance",
    503: "the website may be down, overloaded or under maintenance",
    504: "the website may be down, overloaded or under maintenance",
}


def describe_http_status(status_code: int) -> str:
    """Says what an error status code from a website means, e.g. "HTTP 403 Forbidden, so the website refused access
    and may be blocking the scanner".

    Args:
        status_code: The status code the website answered with.

    Returns:
        The status code, its name and, for the common ones, what it means.
    """
    try:
        label: str = f"HTTP {status_code} {HTTPStatus(status_code).phrase}"
    except ValueError:
        label = f"HTTP {status_code}"
    explanation: str | None = HTTP_STATUS_EXPLANATIONS.get(status_code)
    if explanation is None and status_code >= 500:
        explanation = HTTP_STATUS_EXPLANATIONS[500]
    return f"{label}, so {explanation}" if explanation else label


class DatabaseError(Exception):
    """Base class for all custom database-related exceptions in the application."""

    ...


class NotFoundError(DatabaseError):
    """Exception raised when a requested database record cannot be found.

    Handles initialisation via either a primary key UUID or a dictionary of
    query attributes to build descriptive error messages.

    Attributes:
        id: The UUID of the missing record, if supplied.
        attributes: Key-value attributes used during the lookup, if supplied.
    """

    def __init__(
        self,
        id: uuid.UUID | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if id:
            self.id: uuid.UUID = id
            super().__init__(f"{id=} not found.")
        elif attributes:
            self.attributes: dict[str, Any] = attributes
            super().__init__(f"{attributes=} not found.")
        else:
            super().__init__("not found.")


class IntegrityError(DatabaseError):
    """Exception raised when database integrity or unique constraints are violated.

    Attributes:
        args: Positional argument tuple containing the standard error message string.
    """

    def __init__(self) -> None:
        super().__init__("Data validation error. Ensure all referenced IDs exist and unique constraints are met.")


class WebsiteAlreadyMonitoredError(DatabaseError):
    """Exception raised when adding a website that is already being monitored.

    The address can be written differently, e.g. with or without "www.", a trailing "/", or http instead of https.

    Attributes:
        url: The address of the website already being monitored, as it is saved.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} is already being monitored.")


class UndeliverableEmailError(Exception):
    """Exception raised when an email to an address bounces, so the address cannot receive email.

    Attributes:
        address: The email address that could not be emailed.
    """

    def __init__(self, address: str) -> None:
        self.address: str = address
        super().__init__(f"An email to {address} could not be delivered.")


class ReportNotEmailedError(Exception):
    """Exception raised when a scan finished but its report could not be emailed (e.g. the mail server was down).

    The scan and its report are kept, and the report is sent with the next scheduled scan.

    Attributes:
        url: The URL of the website that was scanned.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(
            f"The scan of {url} finished, but its report could not be emailed. "
            "It will be sent with the next scheduled scan."
        )


class AlreadyWatchedError(Exception):
    """Exception raised when a critical page being added is already being watched.

    Attributes:
        url: The URL of the page.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} is already being watched.")


class MainPageNotDeletableError(Exception):
    """Exception raised when deleting a website's main page, which is always watched while the website is.

    Attributes:
        url: The URL of the main page.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} is the website's main page, which is always watched, so it cannot be deleted.")


class InvalidPageError(ValueError):
    """Exception raised when a critical page being added is not a valid URL, or is on a different website."""


class WebCrawlerError(Exception):
    """Base class for all custom web crawler exceptions in the application."""

    ...


class PageNotLoadedError(WebCrawlerError):
    """Exception raised when a website or critical page being added cannot be loaded, so it is not added.

    Attributes:
        url: The URL of the page that could not be loaded.
        reason: Why it could not be loaded, e.g. "HTTP 404 Not Found, so the page does not exist or has moved".
    """

    def __init__(self, url: str, reason: str | None = None) -> None:
        self.url: str = url
        self.reason: str | None = reason
        if reason:
            super().__init__(f"{url} could not be loaded. {reason}.")
        else:
            super().__init__(f"{url} could not be loaded. Check it exists and the URL is correct.")


class TrafficError(WebCrawlerError):
    """Exception raised when web scraping exceeds server rate limits or encounters traffic blocks.

    Attributes:
        url: The target URL string that triggered the traffic error.
        status_code: The HTTP status code returned by the server (e.g., 429, 403, 503).
        message: What happened, if it says more than the page answering with the status code, e.g. that many pages
            answered 403 at once.
        retry_after_seconds: How long the server asked to be left before the next request, if it said.
    """

    def __init__(
        self, url: str, status_code: int, message: str | None = None, retry_after_seconds: float | None = None
    ) -> None:
        self.url: str = url
        self.status_code: int = status_code
        self.message: str | None = message
        self.retry_after_seconds: float | None = retry_after_seconds

        super().__init__(message or f"{url} kept answering {describe_http_status(status_code)}.")


class WebConnectionError(WebCrawlerError):
    """Exception raised when network connection timeouts or link drops occur during a crawl operation.

    Attributes:
        url: The target URL string that failed to establish or maintain a connection.
        reason: Why it could not be reached, e.g. "The website took too long to answer", if known.
    """

    def __init__(self, url: str, reason: str | None = None) -> None:
        self.url: str = url
        self.reason: str | None = reason
        super().__init__(f"{url} could not be reached. {reason or 'The connection failed or timed out'}.")


class WebsiteUnavailableError(WebConnectionError):
    """Exception raised when a website's home page cannot be loaded (e.g. it returns 403, 404 or 500), so none of the
    website could be crawled.

    It is treated as the website being unreachable, rather than as every page on it having been removed.

    Attributes:
        url: The URL of the website's home page.
        reason: Why the home page could not be loaded, e.g. "HTTP 403 Forbidden, so the website refused access and
            may be blocking the scanner", if known.
    """

    def __init__(self, url: str, reason: str | None = None) -> None:
        self.url: str = url
        self.reason: str | None = reason
        message: str = f"The home page {url} could not be loaded, so the website could not be scanned."
        WebCrawlerError.__init__(self, f"{message} {reason}." if reason else message)


class MostPagesMissingError(WebCrawlerError):
    """Exception raised when a crawl finds so few of a website's known pages that the website is probably partly down,
    rather than really having removed them.

    Attributes:
        url: The URL of the website.
        missing_count: How many of the pages found by the last scan were not found.
        known_count: How many pages the last scan found.
    """

    def __init__(self, url: str, missing_count: int, known_count: int) -> None:
        self.url: str = url
        self.missing_count: int = missing_count
        self.known_count: int = known_count
        super().__init__(
            f"{missing_count:,} of the {known_count:,} pages found on {url} by its last scan could not be found, "
            "so the website may be partly down. None of its pages have been reported as removed."
        )


class NotAWebPageError(WebCrawlerError):
    """Exception raised when a URL is a file (e.g. a PDF or an image) rather than a web page, so it is not read.

    Attributes:
        url: The URL of the file.
        content_type: What the server said the file is, e.g. "application/pdf".
    """

    def __init__(self, url: str, content_type: str) -> None:
        self.url: str = url
        self.content_type: str = content_type
        super().__init__(f"{url} is not a web page ({content_type}).")


class StandInPageError(WebCrawlerError):
    """Exception raised when a page loads but has lost most of its text, so what was served is almost certainly a
    stand-in for the real page (e.g. a "Just a moment..." browser check, a maintenance page or a login wall).

    Attributes:
        url: The URL of the page that was served without its content.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} loaded, but most of its content is missing.")


class WebsiteTooLargeError(WebCrawlerError):
    """Exception raised when a website has more pages than the crawler will scan.

    Attributes:
        url: The URL of the website that is too large to scan.
        max_pages: The most pages the crawler would scan.
    """

    def __init__(self, url: str, max_pages: int) -> None:
        self.url: str = url
        self.max_pages: int = max_pages
        super().__init__(f"{url} has more than {max_pages:,} pages, which is more than the crawler will scan.")


class ScanError(Exception):
    """Base class for the errors of the queue that runs website scans one at a time (see `ScanQueue`)."""

    ...


class ScanAlreadyQueuedError(ScanError):
    """Exception raised when a scan is requested for a website that is already queued or being scanned.

    Attributes:
        url: The URL of the website that is already queued or being scanned.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} is already queued or being scanned.")


class ScanCancelledError(ScanError):
    """Exception raised when a website's scan is cancelled before it finished.

    Attributes:
        url: The URL of the website whose scan was cancelled.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"The scan of {url} was cancelled.")
