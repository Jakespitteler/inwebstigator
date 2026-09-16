from httpx2 import AsyncClient, Response


async def fetch_content_from_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Fetches raw HTML content from a given URL asynchronously. Follows redirects to the url that is returned is

    Args:
        client (httpx2.AsyncClient): The HTTPX2 asynchronous client instance used for the request.
        url (str): The target URL to fetch content from.

    Returns:
        tuple[str, str]: The raw HTML text content and the final URL (after any redirects).

    Raises:
        httpx2.HTTPStatusError: If the HTTP response returns an unsuccessful status code (4xx or 5xx).
        httpx2.RequestError: If a network connection error, timeout, or request failure occurs.

    """
    response: Response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.text, str(response.url)
