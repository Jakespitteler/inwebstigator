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


class UserError(Exception):
    """Base class for all custom user exceptions in the application."""

    ...


class NotLoggedInError(UserError):
    """Exception raised when we try and perform an operation and a user is not logged.

    Attributes:
        args: Positional argument tuple containing the standard error message string.
    """

    def __init__(self) -> None:
        super().__init__("User not logged in")


class InvalidCredentials(UserError):
    """Exception raised when we try to log in with the wrong credentials.

    Attributes:
        args: Positional argument tuple containing the standard error message string.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"Invalid credentials: {message}")
