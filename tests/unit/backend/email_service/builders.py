import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from app.models.content_block_models import ChangedBlock, ContentBlock, HTMLBlockType
from app.models.critical_page_models import CriticalPageRead
from app.models.scan_run_models import ChangeCreate, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteRead

WEBSITE_ID: uuid.UUID = uuid.uuid4()
WEBSITE_URL: str = "https://example.gov.au/"
PAGE_URL: str = "https://example.gov.au/fees"


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


def make_scan_run(
    status: ScanStatus = ScanStatus.SUCCESS,
    message: str | None = None,
    changes: Sequence[ChangeCreate] = (),
    scanned_at: datetime = datetime(2026, 10, 7, 10, 56, tzinfo=UTC),
    notified_at: datetime | None = None,
) -> ScanRunRead:
    """Builds a scan of the website, with what it found."""
    return ScanRunRead.model_validate(
        {
            "id": uuid.uuid4(),
            "website_id": WEBSITE_ID,
            "scanned_at": scanned_at,
            "status": status,
            "message": message,
            "notified_at": notified_at,
            "changes": [change.model_dump() for change in changes],
        }
    )
