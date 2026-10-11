import pytest

from app.models.critical_page_models import CriticalPageRead
from app.models.internal_link_models import InternalLinkRead
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteRead


@pytest.fixture
def populated_website(
    test_website: WebsiteRead,
    test_internal_link: InternalLinkRead,
    test_critical_page: CriticalPageRead,
    test_recipient: RecipientRead,
) -> WebsiteRead:
    """Provides a WebsiteRead model fully populated with its internal relationships and recipients."""
    return test_website.model_copy(
        update={
            "internal_link_count": 1,  # test_internal_link
            "critical_pages": [test_critical_page],
            "recipients": [test_recipient],
        }
    )
