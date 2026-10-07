import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl
from sqlalchemy.orm import Session

from app.core.config import config
from app.core.errors import NotFoundError
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate
from app.models.internal_link_models import InternalLinkCreate, InternalLinkRead
from app.models.website_models import DeactivationReason, WebsiteCreate, WebsiteRead, WebsiteUpdate


def test_get_all_websites(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests retrieving a list of all websites.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_websites: Sequence[WebsiteRead] = WebsiteService(session).get_all()
    assert len(fetched_websites) >= 1
    assert any(c.id == test_website.id for c in fetched_websites)
    assert any(c.url == test_website.url for c in fetched_websites)


def test_get_website(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests retrieving an existing website by ID.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)

    assert fetched_website is not None
    assert fetched_website.id == test_website.id
    assert fetched_website.url == test_website.url


def test_get_website_raises_not_found(session: Session) -> None:
    """
    Tests that retrieving a non-existent website ID raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=uuid.uuid4())


def test_get_website_by_url(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests retrieving a website record by its URL.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_website: WebsiteRead = WebsiteService(session).get_by_url(url=test_website.url)

    assert fetched_website is not None
    assert fetched_website.id == test_website.id
    assert fetched_website.url == test_website.url


def test_get_website_by_url_raises_not_found(session: Session) -> None:
    """
    Tests that retrieving a non-existent URL raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).get_by_url(url=HttpUrl("https://www.nonexistent_website.com"))


def test_create_website(session: Session) -> None:
    """
    Tests creating a new website with basic details.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(url=HttpUrl("https://www.test_website.com"))

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url
    assert [page.url for page in created_website.critical_pages] == [website_details.url]
    assert [page.url for page in fetched_website.critical_pages] == [website_details.url]


def test_create_website_with_links_and_critical_pages(session: Session) -> None:
    """
    Tests creating a new website with attached critical pages.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(
        url=HttpUrl("https://www.test_website.com"),
        critical_pages=["https://www.test_website.com/critical_page"],
    )

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url
    assert fetched_website.critical_pages
    assert {page.url for page in fetched_website.critical_pages} == {
        website_details.url,
        HttpUrl("https://www.test_website.com/critical_page"),
    }


def test_create_website_watches_each_critical_page_once(session: Session) -> None:
    """
    Tests a critical page given twice, or that is the main page, is only watched once, even when written with or
    without a trailing slash or "www.", or with capitals in the domain.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(
        url=HttpUrl("https://www.test_website.com"),
        critical_pages=[
            "/",
            "https://www.test_website.com/news/",
            "/news",
            "https://test_website.com/",
            "https://TEST_WEBSITE.com/news",
        ],
    )

    created_website: WebsiteRead = WebsiteService(session).create(website_details)

    assert [page.url for page in created_website.critical_pages] == [
        HttpUrl("https://www.test_website.com/"),
        HttpUrl("https://www.test_website.com/news/"),
    ]


def test_get_by_url_selects_requested_website(session: Session, test_website: WebsiteRead) -> None:
    service = WebsiteService(session)
    second = service.create(WebsiteCreate(url=HttpUrl("https://second.example.com/au")))
    assert service.get_by_url(second.url).id == second.id
    assert service.get_by_url(test_website.url).id == test_website.id
    with pytest.raises(NotFoundError):
        service.get_by_url(HttpUrl("https://unknown.example.com"))


def test_create_website_with_recipients(session: Session) -> None:
    """
    Tests creating a website with notification recipients.

    Args:
        session: The database session fixture.
    """
    recipient_emails = [
        "recipient_one@email.com",
        "recipient_two@email.com",
    ]
    website_details = WebsiteCreate(
        url=HttpUrl("https://www.test_recipient_website.com"),
        recipient_emails=recipient_emails,
    )

    created_website: WebsiteRead = WebsiteService(session).create(website_details)

    assert created_website.id is not None
    assert len(created_website.recipients) == 2
    assert {recipient.email for recipient in created_website.recipients} == set(recipient_emails)


def test_update_website(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests updating an existing website's details.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    model_update = WebsiteUpdate(url=HttpUrl("https://www.updated_website.com"))

    updated_website: WebsiteRead = WebsiteService(session).update(id=test_website.id, model_update=model_update)
    assert updated_website.id == test_website.id
    assert updated_website.url == model_update.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)
    assert fetched_website.url == model_update.url


def test_get_website_counts_its_internal_links(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests a website read from the database carries a count of its internal links, rather than every link.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    urls = [HttpUrl(f"{test_website.url}page/{number}") for number in range(3)]
    InternalLinkService(session).create_batch(urls=urls, website_id=test_website.id)

    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)

    assert fetched_website.internal_link_count == 3
    assert "internal_links" not in WebsiteRead.model_fields


def test_update_website_internal_links(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests updating a website with added and removed internal links.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    internal_link_service = InternalLinkService(session)
    existing_link_url = HttpUrl(f"{test_website.url}link_to_delete")
    internal_link_service.create_batch(urls=[existing_link_url], website_id=test_website.id)

    new_link_url = HttpUrl(f"{test_website.url}link_to_add")
    model_update = WebsiteUpdate(
        recent_added_internal_links=[new_link_url],
        recent_removed_internal_links=[existing_link_url],
    )

    updated_website = WebsiteService(session).update(id=test_website.id, model_update=model_update)

    # Confirm added link exists
    added_link = internal_link_service.get_by_url(url=new_link_url)
    assert added_link.website_id == test_website.id

    # Confirm deleted link is gone
    with pytest.raises(NotFoundError):
        internal_link_service.get_by_url(url=existing_link_url)

    # Confirm the website read model counts its links, rather than loading them all
    assert updated_website.internal_link_count == len(internal_link_service.get_urls_for_website(test_website.id))
    assert str(new_link_url) in internal_link_service.get_urls_for_website(test_website.id)


def test_delete_website(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests deleting an existing website and cascading its children.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    WebsiteService(session).delete(id=test_website.id)

    # Check the website can no longer be retrieved
    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=test_website.id)


def test_delete_website_raises_not_found(session: Session) -> None:
    """
    Tests that deleting a non-existent website raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    with pytest.raises(NotFoundError):
        WebsiteService(session).delete(id=uuid.uuid4())


def test_delete_website_cascades(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests deleting an existing website and cascading its children.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    # create link and page to be deleted on cascade
    created_internal_link: InternalLinkRead = InternalLinkService(session).create(
        model_create=InternalLinkCreate(
            url=HttpUrl(f"{test_website.url}internal_link"),
            website_id=test_website.id,
        )
    )
    created_critical_page: CriticalPageRead = CriticalPageService(session).create(
        model_create=CriticalPageCreate(
            url=HttpUrl(f"{test_website.url}critical_page"),
            website_id=test_website.id,
        )
    )
    WebsiteService(session).delete(id=test_website.id)

    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=test_website.id)

    with pytest.raises(NotFoundError):
        InternalLinkService(session).get(id=created_internal_link.id)

    with pytest.raises(NotFoundError):
        CriticalPageService(session).get(id=created_critical_page.id)


def test_update_website_critical_page_updates(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests updating a website's critical pages using critical_page_updates.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    critical_page_service = CriticalPageService(session)
    created_page = critical_page_service.create(
        model_create=CriticalPageCreate(
            url=HttpUrl(f"{test_website.url}critical_1"),
            website_id=test_website.id,
        )
    )

    updated_url = HttpUrl(f"{test_website.url}critical_1_updated")
    model_update = WebsiteUpdate(critical_page_updates={created_page.id: CriticalPageUpdate(url=updated_url)})

    WebsiteService(session).update(id=test_website.id, model_update=model_update)

    fetched_page = critical_page_service.get(id=created_page.id)
    assert fetched_page.url == updated_url


def test_set_cooldown(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests setting a cooldown period on a website.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    hours = 12
    updated_website: WebsiteRead = WebsiteService(session).set_cooldown(id=test_website.id, hours=hours)

    assert updated_website.on_cooldown_until is not None
    # Verify the cooldown is set to a future timestamp
    assert updated_website.on_cooldown_until > datetime.now()


def test_throttle_and_cooldown(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests throttling parameters (increasing delay, decreasing concurrency)
    and placing a website on cooldown.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    initial_delay = test_website.recommended_delay
    initial_concurrent = test_website.recommended_concurrent

    hours = 24
    updated_website: WebsiteRead = WebsiteService(session).throttle_and_cooldown(id=test_website.id, hours=hours)

    assert updated_website.on_cooldown_until is not None
    assert updated_website.recommended_delay > initial_delay
    assert updated_website.recommended_concurrent <= initial_concurrent
    assert updated_website.on_cooldown_until > datetime.now()


def test_throttle_and_cooldown_clamped_to_config_limits(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests that throttling respects maximum delay and minimum concurrency limits defined in config.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Force site attributes to maximum/minimum bounds before throttling
    service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
        ),
    )

    updated_website = service.throttle_and_cooldown(id=test_website.id)

    assert updated_website.recommended_delay == config.web_crawler_max_delay
    assert updated_website.recommended_concurrent == config.web_crawler_min_concurrent


def test_handle_traffic_error_throttles_when_not_at_min_speed(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error when website crawler parameters have not reached minimum speed limits.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Configure website to be above minimum speed limits
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay - 0.1,
            recommended_concurrent=config.web_crawler_min_concurrent + 1,
        ),
    )

    result_message = service.handle_traffic_error(website=website)

    assert result_message == "Website throttled and placed on cooldown."

    fetched_website = service.get(id=website.id)
    assert fetched_website.on_cooldown_until is not None
    assert fetched_website.on_cooldown_until > datetime.now()


def test_handle_traffic_error_increments_attempts_at_min_speed(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error increments failed attempts when already operating at minimum speed.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    # Configure website to be at minimum speed limits
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
            failed_attempts_at_min_speed=0,
        ),
    )

    result_message = service.handle_traffic_error(website=website)

    assert result_message == "Website placed on cooldown."

    fetched_website = service.get(id=website.id)
    assert fetched_website.failed_attempts_at_min_speed == 1
    assert fetched_website.on_cooldown_until is not None


def test_handle_traffic_error_deactivates_website_at_max_failures(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_traffic_error deactivates the website upon reaching maximum failed attempts at minimum speed.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)
    max_failures = config.web_crawler_max_failed_attempts_at_min_speed

    # Set website to max failure threshold
    website = service.update(
        id=test_website.id,
        model_update=WebsiteUpdate(
            recommended_delay=config.web_crawler_max_delay,
            recommended_concurrent=config.web_crawler_min_concurrent,
            failed_attempts_at_min_speed=max_failures,
            active=True,
        ),
    )

    result_message = service.handle_traffic_error(website=website)

    assert f"Failed {max_failures} times. Deactivating: {website.url}" in result_message

    fetched_website = service.get(id=website.id)
    assert fetched_website.active is False


def test_handle_connection_error(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_connection_error sets a 2-hour cooldown period and returns proper status message.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    result_message = service.handle_connection_error(website_id=test_website.id)

    assert result_message == "Website placed on cooldown."

    fetched_website = service.get(id=test_website.id)
    assert fetched_website.on_cooldown_until is not None
    assert fetched_website.on_cooldown_until > datetime.now() + timedelta(hours=1, minutes=59)


def test_handle_too_large_deactivates_website_and_records_why(session: Session, test_website: WebsiteRead) -> None:
    """
    Tests handle_too_large deactivates the website, records it was too large, and explains the page limit.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    service = WebsiteService(session)

    result_message = service.handle_too_large(id=test_website.id, max_pages=50_000)

    assert "more than 50,000 pages" in result_message

    fetched_website = service.get(id=test_website.id)
    assert fetched_website.active is False
    assert fetched_website.deactivated_reason == DeactivationReason.TOO_LARGE


@pytest.mark.parametrize(
    ("model_update", "expected_reason"),
    [
        (WebsiteUpdate(active=True), None),
        (WebsiteUpdate(active=False, recommended_delay=2), DeactivationReason.TOO_LARGE),
        (WebsiteUpdate(recommended_delay=2), DeactivationReason.TOO_LARGE),
    ],
    ids=["re-activated", "saved-while-inactive", "other-setting-changed"],
)
def test_deactivated_reason_is_only_cleared_by_reactivating(
    session: Session,
    test_website: WebsiteRead,
    model_update: WebsiteUpdate,
    expected_reason: DeactivationReason | None,
) -> None:
    """
    Tests why a website was deactivated is kept until the website is re-activated.

    Args:
        session: The database session fixture.
        test_website: The test website record.
        model_update: The update made after the website was deactivated.
        expected_reason: The deactivation reason expected after the update.
    """
    service = WebsiteService(session)
    service.handle_too_large(id=test_website.id, max_pages=50_000)

    updated_website = service.update(id=test_website.id, model_update=model_update)

    assert updated_website.deactivated_reason == expected_reason
