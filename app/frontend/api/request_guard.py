from collections.abc import Awaitable, Callable, Collection
from urllib.parse import urlsplit

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from app.core.config import config

API_TOKEN_HEADER: str = "X-Inwebstigator-Token"
READ_ONLY_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

type NextHandler = Callable[[Request], Awaitable[Response]]


def is_allowed_host(host_header: str, allowed_hosts: Collection[str]) -> bool:
    """Checks a request's Host header names this computer, e.g. "127.0.0.1:48731".

    This stops "DNS rebinding", where a website points its own name at this computer so a page from it can read the
    dashboard (including its token).

    Args:
        host_header: The request's Host header.
        allowed_hosts: The host names the app answers to, e.g. "127.0.0.1" and "localhost".

    Returns:
        True if the host is one of them, whatever the port.
    """
    host_name: str | None = urlsplit(f"//{host_header}").hostname
    return host_name is not None and host_name in allowed_hosts


def has_api_token(request: Request, api_token: str) -> bool:
    """Checks a request that changes something carries the token the dashboard page was given.

    A page on another website can make the browser send a request to the app, but it cannot read the token, and
    sending the token's header needs the app's permission (CORS), which it never gives.

    Args:
        request: The request.
        api_token: The token for this run of the app.

    Returns:
        True if the request only reads, or carries the right token.
    """
    return request.method in READ_ONLY_METHODS or request.headers.get(API_TOKEN_HEADER) == api_token


async def only_accept_requests_from_the_dashboard(request: Request, call_next: NextHandler) -> Response:
    """Middleware that refuses requests that did not come from the app's own dashboard.

    The app has no login, and it runs on the user's computer, so without this a website open in their normal browser
    could start scans, add websites or email their recipients. Can be turned off for development with
    `API_TOKEN_REQUIRED=false`.

    Args:
        request: The request.
        call_next: Handles the request if it is allowed.

    Returns:
        The response, or a 400 (unknown host) or 403 (missing or wrong token) error.
    """
    if not config.api_token_required:
        return await call_next(request)
    if not is_allowed_host(request.headers.get("host", ""), config.allowed_hosts):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "Unknown host."})
    if not has_api_token(request, config.api_token):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "This request did not come from the Inwebstigator dashboard."},
        )
    return await call_next(request)
