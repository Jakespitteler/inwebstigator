import uuid
from typing import Any


class DataBaseError(Exception):
    """Base class for all custom database-related exceptions in the application."""

    ...


class NotFoundError(DataBaseError):
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


class IntegrityError(DataBaseError):
    """Exception raised when database integrity or unique constraints are violated.

    Attributes:
        args: Positional argument tuple containing the standard error message string.
    """

    def __init__(self) -> None:
        super().__init__("Data validation error. Ensure all referenced IDs exist and unique constraints are met.")


class UndeliverableEmailError(Exception):
    """Exception raised when an email to an address bounces, so the address cannot receive email.

    Attributes:
        address: The email address that could not be emailed.
    """

    def __init__(self, address: str) -> None:
        self.address: str = address
        super().__init__(f"An email to {address} could not be delivered.")


class WebCrawlerError(Exception):
    """Base class for all custom web crawler exceptions in the application."""

    ...


class TrafficError(WebCrawlerError):
    """Exception raised when web scraping exceeds server rate limits or encounters traffic blocks.

    Attributes:
        url: The target URL string that triggered the traffic error.
        status_code: The HTTP status code returned by the server (e.g., 429, 403, 503).
    """

    def __init__(self, url: str, status_code: int, message: str | None = None) -> None:
        self.url: str = url
        self.status_code: int = status_code
        self.message: str | None = message

        error_message: str = f"Traffic issue ({status_code}) at {url=}. Crawler is overwhelming the server.."
        if message:
            error_message += message

        super().__init__(error_message)


class WebConnectionError(WebCrawlerError):
    """Exception raised when network connection timeouts or link drops occur during a crawl operation.

    Attributes:
        url: The target URL string that failed to establish or maintain a connection.
    """

    def __init__(self, url: str) -> None:
        super().__init__(f"Network traffic issue (Timeout/Connection drop) reaching {url=}.")


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


class ScanAlreadyQueuedError(WebCrawlerError):
    """Exception raised when a scan is requested for a website that is already queued or being scanned.

    Attributes:
        url: The URL of the website that is already queued or being scanned.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"{url} is already queued or being scanned.")


class ScanCancelledError(WebCrawlerError):
    """Exception raised when a website's scan is cancelled before it finished.

    Attributes:
        url: The URL of the website whose scan was cancelled.
    """

    def __init__(self, url: str) -> None:
        self.url: str = url
        super().__init__(f"The scan of {url} was cancelled.")
