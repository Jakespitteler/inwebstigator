import logging

from httpx2 import AsyncClient

from app.backend.diff_checker import compare_page_content, find_link_difference
from app.backend.site_crawler import crawl_site
from app.backend.utils.html_parser import parse_html
from app.backend.utils.http_client import fetch_content_from_url
from app.backend.utils.links import extract_links_from_html, separate_document_links
from app.core.config import config
from app.models.critical_page_models import CriticalPageRead, CriticalPageUpdate
from app.models.website_models import WebsiteRead, WebsiteUpdate

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES: int = config.web_crawler_default_max_pages
DEFAULT_DELAY: float = config.web_crawler_default_delay
DEFAULT_CONCURRENT: int = config.web_crawler_default_concurrent
BATCH_402_THRESHOLD_SECONDS: int = config.web_crawler_batch_402_threshold_seconds


async def get_critical_page_updates(client: AsyncClient, stored_page: CriticalPageRead) -> CriticalPageUpdate:
    updates = CriticalPageUpdate(url=stored_page.url)

    text_body, _ = await fetch_content_from_url(client, url=stored_page.url)
    all_links: list[str] = extract_links_from_html(url=stored_page.url, html_content=text_body)
    documents, links = separate_document_links(links=all_links)

    updates.recent_documents_added, updates.recent_documents_removed = find_link_difference(
        previous_state=stored_page.documents or [],
        current_state=documents,
    )
    logger.info(f"{updates.recent_documents_added=}")
    logger.info(f"{updates.recent_documents_removed=}")
    if updates.recent_documents_added or updates.recent_documents_removed:
        updates.documents = documents

    updates.recent_links_added, updates.recent_links_removed = find_link_difference(
        previous_state=stored_page.links or [],
        current_state=links,
    )
    logger.info(f"{updates.recent_links_added=}")
    logger.info(f"{updates.recent_links_removed=}")
    if updates.recent_links_added or updates.recent_links_removed:
        updates.links = links

    updates.recent_text_added, updates.recent_text_removed, updates.recent_text_changed = compare_page_content(
        old_content=parse_html(html=stored_page.text_body or ""),
        new_content=parse_html(html=text_body),
    )
    logger.info(f"{updates.recent_text_added=}")
    logger.info(f"{updates.recent_text_removed=}")
    logger.info(f"{updates.recent_text_changed=}")

    if updates.recent_text_added or updates.recent_text_removed or updates.recent_text_changed:
        updates.text_body = text_body

    return updates


async def get_website_updates(
    client: AsyncClient,
    stored_website: WebsiteRead,
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
) -> WebsiteUpdate:
    updates = WebsiteUpdate(url=stored_website.url)

    updates.critical_page_updates = (
        {cp.id: await get_critical_page_updates(client, cp) for cp in stored_website.critical_pages}
        if stored_website.critical_pages
        else None
    )

    current_internal_links: list[str] = list(
        await crawl_site(
            client,
            url=stored_website.url,
            delay=delay or stored_website.recommended_delay,
            max_concurrent=concurrent or stored_website.recommended_concurrent,
            max_pages=max_pages or DEFAULT_MAX_PAGES,
            batch_403_threshold=BATCH_402_THRESHOLD_SECONDS,
        )
    )

    updates.recent_added_internal_links, updates.recent_removed_internal_links = find_link_difference(
        previous_state=[link.url for link in stored_website.internal_links],
        current_state=current_internal_links,
    )
    logger.info(f"{updates.recent_added_internal_links=}")
    logger.info(f"{updates.recent_removed_internal_links=}")
    return updates
