from collections.abc import Sequence

from httpx2 import AsyncClient, HTTPError

from app.backend.crawler.page_fetcher import fetch_content_from_url
from app.core.errors import PageNotLoadedError, WebCrawlerError


async def check_pages_exist(client: AsyncClient, urls: Sequence[str]) -> None:
    """Loads each page before it is added, so websites and critical pages that do not exist are not added.

    Args:
        client: The HTTP client for making web requests.
        urls: The full URLs of the pages to check.

    Raises:
        PageNotLoadedError: If a page could not be loaded.
    """
    for url in urls:
        try:
            await fetch_content_from_url(client, url)
        except (WebCrawlerError, HTTPError) as error:
            raise PageNotLoadedError(url) from error
