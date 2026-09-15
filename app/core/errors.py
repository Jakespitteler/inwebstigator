import uuid
from typing import Any


class DataBaseError(Exception):
    """Base class for all database exceptions."""

    ...


class NotFoundError(DataBaseError):
    """
    Exception raised when a record is not found.

    Attributes:
        id: The UUID of the record that was not found.
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
    """
    Exception raised when data integrity constraints are violated.
    """

    def __init__(self) -> None:
        super().__init__("Data validation error. Ensure all referenced IDs exist and unique constraints are met.")


class WebCrawlerError(Exception):
    """Base class for all web crawler exceptions."""

    ...


class TrafficError(WebCrawlerError):
    """
    Exception raised when a website's rate limits have been exceeded or the server is overloaded.
    """

    def __init__(self, url: str, status_code: int) -> None:
        self.url: str = url
        self.status_code: int = status_code
        super().__init__(f"Traffic issue ({status_code}) at {url=}. Crawler is overwhelming the server..")


class WebConnectionError(WebCrawlerError):
    """
    Exception raised when a website's drops out or loses connection.
    """

    def __init__(self, url: str) -> None:
        super().__init__(f"Network traffic issue (Timeout/Connection drop) reaching {url=}.")
