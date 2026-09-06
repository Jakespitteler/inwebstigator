from collections.abc import Collection, Mapping
from typing import TypedDict

from sqlalchemy.orm import Session

from app.db.errors import NotFoundError
from app.db.models.internal_link_models import InternalLinkCreateBatch
from app.db.models.website_models import WebsiteCreate
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService


class LinkChanges(TypedDict):
    added: list[str]
    removed: list[str]


def compare_and_update_links(
    current_data: Mapping[str, Collection[str]],
    session: Session,
) -> dict[str, LinkChanges]:
    """
    Compare crawled links with stored links and update the database.

    The caller controls the database transaction through get_db_session().

    Args:
        current_data:
            Mapping of website URLs to their currently discovered links.
        session:
            Active SQLAlchemy database session.

    Returns:
        Added and removed links for each website.
    """
    website_service = WebsiteService(session)
    internal_link_service = InternalLinkService(session)

    changes: dict[str, LinkChanges] = {}

    for website_url, current_links in current_data.items():
        new_links = set(current_links)

        try:
            website = website_service.get_by_url(website_url)
        except NotFoundError:
            website_service.create(
                WebsiteCreate(
                    url=website_url,
                    internal_links=sorted(new_links),
                )
            )

            changes[website_url] = {
                "added": sorted(new_links),
                "removed": [],
            }
            continue

        existing_links = {
            internal_link.url: internal_link
            for internal_link in website.internal_links
        }

        old_links = set(existing_links)

        added = new_links - old_links
        removed = old_links - new_links

        if added:
            internal_link_service.create_batch(
                InternalLinkCreateBatch(
                    urls=sorted(added),
                    website_id=website.id,
                )
            )

        for removed_url in removed:
            internal_link_service.delete(
                id=existing_links[removed_url].id
            )

        changes[website_url] = {
            "added": sorted(added),
            "removed": sorted(removed),
        }

    return changes