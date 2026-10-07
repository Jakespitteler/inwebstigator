import uuid
from datetime import datetime

from app.backend.diff_checker.models import ChangedBlock, ContentBlock, HTMLBlockType
from app.models.critical_page_models import CriticalPageRead
from app.models.website_models import WebsiteRead

WEBSITE_ID: uuid.UUID = uuid.uuid4()


def make_block(text: str, heading: str | None = "Fees") -> ContentBlock:
    """Builds a paragraph block under a heading."""
    return ContentBlock(parent_heading=heading, block_type=HTMLBlockType.PARAGRAPH, text=text)


def make_change(old: ContentBlock, new: ContentBlock) -> ChangedBlock:
    """Builds a change from one block to another."""
    return ChangedBlock(old_block=old, new_block=new, similarity=0.9)


def make_page(url: str = "https://example.gov.au/fees", **fields: object) -> CriticalPageRead:
    """Builds a critical page, with any of its fields set."""
    return CriticalPageRead.model_validate({"id": uuid.uuid4(), "website_id": WEBSITE_ID, "url": url, **fields})


def make_website(
    url: str = "https://example.gov.au/",
    critical_pages: list[CriticalPageRead] | None = None,
    last_scan_at: datetime | None = None,
    **fields: object,
) -> WebsiteRead:
    """Builds a website that is working normally, with any of its fields set."""
    return WebsiteRead.model_validate(
        {
            "id": WEBSITE_ID,
            "url": url,
            "recipients": [],
            "recommended_delay": 0.5,
            "recommended_concurrent": 5,
            "days_between_scans": 1,
            "last_scan_at": last_scan_at,
            "active": True,
            "failed_attempts_at_min_speed": 0,
            "critical_pages": critical_pages or [],
            "internal_link_count": 0,
            **fields,
        }
    )
