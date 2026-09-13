import logging

from httpx2 import AsyncClient

from app.backend.page_inspector.compare_content import compare_content
from app.backend.utils.html_extractor import extract_links_from_html, fetch_content_from_url
from app.backend.utils.html_parser import extract_content
from app.backend.utils.links import find_link_difference, separate_document_links
from app.backend.web_scraper.site_crawler import crawl_site
from app.core.config import config
from app.db.models.critical_page_models import CriticalPageRead, CriticalPageState
from app.db.models.website_models import WebsiteRead, WebsiteState

logger = logging.getLogger(__name__)

MAX_PAGES: int = config.web_crawler_max_pages
BATCH_402_THRESHOLD_SECONDS: int = config.web_crawler_batch_402_threshold_seconds


async def get_critical_page_state(client: AsyncClient, stored_page: CriticalPageRead) -> CriticalPageState:
    state = CriticalPageState(id=stored_page.id, url=stored_page.url)

    text_body, _ = await fetch_content_from_url(client, url=stored_page.url)
    all_links: list[str] = extract_links_from_html(url=state.url, html_content=text_body)
    documents, links = separate_document_links(links=all_links)

    state.documents_added, state.documents_removed = find_link_difference(
        previous_state=stored_page.documents or [],
        current_state=documents,
    )
    logger.info(f"{state.documents_added=}")
    logger.info(f"{state.documents_removed=}")
    if state.documents_added or state.documents_removed:
        state.updates.documents = documents

    state.links_added, state.links_removed = find_link_difference(
        previous_state=stored_page.links or [],
        current_state=links,
    )
    logger.info(f"{state.links_added=}")
    logger.info(f"{state.links_removed=}")
    if state.links_added or state.links_removed:
        state.updates.links = links

    state.text_added, state.text_removed, state.text_changed = compare_content(
        old_content=extract_content(html=stored_page.text_body or ""),
        new_content=extract_content(html=text_body),
    )
    logger.info(f"{state.text_added=}")
    logger.info(f"{state.text_removed=}")
    logger.info(f"{state.text_changed=}")

    if state.text_added or state.text_removed or state.text_changed:
        state.updates.text_body = text_body

    return state


async def get_website_state(
    client: AsyncClient,
    stored_website: WebsiteRead,
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
) -> WebsiteState:
    state = WebsiteState(id=stored_website.id, url=stored_website.url)

    for critical_page in stored_website.critical_pages:
        state.critical_page_states.append(await get_critical_page_state(client, critical_page))

    current_internal_links = list(
        await crawl_site(
            client,
            url=stored_website.url,
            delay=delay or stored_website.recommended_delay,
            max_concurrent=concurrent or stored_website.recommended_concurrent,
            max_pages=max_pages or MAX_PAGES,
            batch_403_threshold=BATCH_402_THRESHOLD_SECONDS,
        )
    )

    state.added_internal_links, state.removed_internal_links = find_link_difference(
        previous_state=[link.url for link in stored_website.internal_links],
        current_state=current_internal_links,
    )
    logger.info(f"{state.added_internal_links=}")
    logger.info(f"{state.removed_internal_links=}")
    return state
