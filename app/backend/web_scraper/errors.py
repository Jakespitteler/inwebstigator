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
