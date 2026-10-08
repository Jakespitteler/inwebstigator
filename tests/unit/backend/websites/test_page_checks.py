from collections.abc import Callable

import httpx2
import pytest

from app.backend.websites.page_checks import check_pages_exist
from app.core.errors import PageNotLoadedError

type RequestHandler = Callable[[httpx2.Request], httpx2.Response]

HOME_PAGE: str = "https://example.com/"
NEWS_PAGE: str = "https://example.com/news"


def _website_where_news_page(news_page: RequestHandler) -> RequestHandler:
    """Makes a website whose home page loads, and whose news page answers as given.

    Args:
        news_page: How the news page answers.

    Returns:
        The website's request handler.
    """

    def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == NEWS_PAGE:
            return news_page(request)
        return httpx2.Response(200, text="<p>Home page.</p>")

    return respond


def _missing(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(404, text="Not Found")


def _broken(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(500, text="Internal Server Error")


def _rate_limited(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(429, text="Too Many Requests")


def _a_pdf(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"})


def _unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("Connection refused", request=request)


def _protocol_error(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.RequestError("Protocol error", request=request)


@pytest.mark.anyio
async def test_pages_that_load_are_accepted() -> None:
    """Tests every page is loaded, and nothing is refused when they all load, including a page that redirects to
    one that loads."""
    requested_urls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == f"{HOME_PAGE}old-news":
            return httpx2.Response(301, headers={"Location": NEWS_PAGE})
        return httpx2.Response(200, text="<p>A page.</p>")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await check_pages_exist(client, [HOME_PAGE, f"{HOME_PAGE}old-news"])

    assert requested_urls == [HOME_PAGE, f"{HOME_PAGE}old-news", NEWS_PAGE]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "news_page",
    [_missing, _broken, _rate_limited, _a_pdf, _unreachable, _protocol_error],
    ids=["missing", "server error", "rate limited", "not a web page", "unreachable", "protocol error"],
)
async def test_a_page_that_cannot_be_loaded_is_refused_by_name(news_page: RequestHandler) -> None:
    """Tests a page that cannot be loaded, for whatever reason, is refused with an error naming that page (not the
    pages that loaded), so the user is told which one to fix."""
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(_website_where_news_page(news_page))) as client:
        with pytest.raises(PageNotLoadedError) as refused:
            await check_pages_exist(client, [HOME_PAGE, NEWS_PAGE])

    assert refused.value.url == NEWS_PAGE
    assert str(refused.value) == f"{NEWS_PAGE} could not be loaded. Check it exists and the URL is correct."
