import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest
from pytest_mock import MockerFixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backend.email_service import delivery
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.websites import recipient_checks
from app.backend.websites.website_setup import (
    MONITORING_STARTED_SUBJECT,
    RECIPIENT_ADDED_SUBJECT,
    add_critical_page,
    add_website,
    delete_website,
    update_website_settings,
)
from app.core.errors import (
    InvalidPageError,
    NotFoundError,
    PageNotLoadedError,
    UndeliverableEmailError,
    WebConnectionError,
)
from app.db.schema import DBChange, DBCriticalPage, DBInternalLink, DBRecipient, DBScanRun, DBWebsite
from app.db.services.recipient_service import RecipientService
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.models import critical_page_models, internal_link_models, website_models
from app.models.scan_run_models import ChangeKind, ScanStatus
from app.models.website_models import WebsiteCreate, WebsiteRead, WebsiteSettingsUpdate
from tests.fakes import FakeEmailSender

type RequestHandler = Callable[[httpx2.Request], httpx2.Response]

pytestmark = pytest.mark.usefixtures("backend_uses_test_session", "empty_scan_queue")

HOME_PAGE: str = "https://example.com/"
FEES_PAGE: str = "https://example.com/fees"
HOME_HTML: str = '<html><body><p>Home page.</p><a href="/fees">Fees</a></body></html>'
FEES_HTML: str = "<html><body><p>The fee is $100.</p></body></html>"


# ==========================
#  Helpers
# ==========================


@pytest.fixture
def bouncing_addresses(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Undoes the autouse fixture that skips confirmations, so the emails are really sent through the fake sender.

    Watching a real inbox for bounces is replaced by a stand-in: a new address in the returned set bounces once it
    has been emailed.

    Returns:
        The addresses that bounce. Add to it to make an address bounce.
    """
    bouncing: set[str] = set()

    def confirm_and_watch_for_bounce(email: OutgoingEmail, email_sender: delivery.EmailSender) -> None:
        delivery.send_confirmation(email, email_sender)
        if email.to in bouncing:
            raise UndeliverableEmailError(email.to)

    monkeypatch.setattr(recipient_checks, "send_confirmation", delivery.send_confirmation)
    monkeypatch.setattr(recipient_checks, "confirm_address_can_receive_email", confirm_and_watch_for_bounce)
    return bouncing


def _put_online(mocker: MockerFixture, respond: RequestHandler) -> list[str]:
    """Makes the backend's HTTP clients answer every request with a handler instead of going online.

    Args:
        mocker: Patches the backend's HTTP client.
        respond: Answers each request.

    Returns:
        The URLs requested, in order.
    """
    requested_urls: list[str] = []

    def record_and_respond(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return respond(request)

    mocker.patch(
        "app.backend.websites.website_setup.new_http_client",
        side_effect=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(record_and_respond)),
    )
    return requested_urls


def _serve(pages: dict[str, str]) -> RequestHandler:
    """Makes a website that serves the given pages, and answers "Not Found" for any other page.

    Args:
        pages: The HTML of each page, by its full URL.

    Returns:
        The website's request handler.
    """

    def respond(request: httpx2.Request) -> httpx2.Response:
        html: str | None = pages.get(str(request.url))
        return httpx2.Response(200, text=html) if html is not None else httpx2.Response(404, text="Not Found")

    return respond


def _new_website(**fields: object) -> WebsiteCreate:
    """Builds a website as the add-website wizard sends it.

    Args:
        fields: The wizard's fields, e.g. the URL, critical pages and recipient emails.

    Returns:
        The website to add.
    """
    return WebsiteCreate.model_validate(fields)


def _count(session: Session, table: type[DBWebsite | DBRecipient | DBCriticalPage]) -> int:
    """Counts the rows saved in a table.

    Args:
        session: The test's database session.
        table: The table to count.

    Returns:
        How many rows it has.
    """
    return session.scalar(select(func.count()).select_from(table)) or 0


# ==========================
#  Adding a website
# ==========================


@pytest.mark.anyio
async def test_adding_a_website_saves_its_first_scan_as_the_baseline(
    session: Session, mocker: MockerFixture, bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests adding a website saves what its pages look like now (with no changes reported), records the scan time
    and sends each recipient one "Website monitoring started" email, and nothing else."""
    _put_online(mocker, _serve({HOME_PAGE: HOME_HTML, FEES_PAGE: FEES_HTML}))
    mocker.patch("app.backend.scanning.change_detection.crawl_site", return_value={HOME_PAGE, FEES_PAGE})

    added: WebsiteRead = await add_website(
        _new_website(url=HOME_PAGE, critical_pages=["/fees"], recipient_emails=["a@example.com", "b@example.com"]),
        email_sender,
    )

    saved: WebsiteRead = WebsiteService(session).get(added.id)
    assert {str(page.url): page.text_body for page in saved.critical_pages} == {
        HOME_PAGE: HOME_HTML,
        FEES_PAGE: FEES_HTML,
    }
    assert saved.last_scan_at is not None
    [first_scan] = ScanRunService(session).get_latest_for_website(added.id, limit=5)
    assert first_scan.changes == []
    assert sorted((email.to, email.subject) for email in email_sender.sent) == [
        ("a@example.com", MONITORING_STARTED_SUBJECT),
        ("b@example.com", MONITORING_STARTED_SUBJECT),
    ]


@pytest.mark.anyio
async def test_a_new_website_with_a_critical_page_that_cannot_be_loaded_is_not_added(
    session: Session, mocker: MockerFixture, bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests a website is not added, and nobody is emailed, when one of its critical pages cannot be loaded."""
    _put_online(mocker, _serve({HOME_PAGE: HOME_HTML}))

    with pytest.raises(PageNotLoadedError) as refused:
        await add_website(
            _new_website(url=HOME_PAGE, critical_pages=["/missing"], recipient_emails=["a@example.com"]), email_sender
        )

    assert refused.value.url == f"{HOME_PAGE}missing"
    assert _count(session, DBWebsite) == 0
    assert email_sender.sent == []


@pytest.mark.anyio
async def test_a_new_website_is_not_added_when_one_recipient_bounces(
    session: Session, mocker: MockerFixture, bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests a website with one address that bounces is refused without adding anything: no website, no critical
    pages and none of its recipients, not even the ones that could be emailed."""
    _put_online(mocker, _serve({HOME_PAGE: HOME_HTML}))
    bouncing_addresses.add("bounces@example.com")

    with pytest.raises(UndeliverableEmailError):
        await add_website(
            _new_website(url=HOME_PAGE, recipient_emails=["fine@example.com", "bounces@example.com"]), email_sender
        )

    assert (_count(session, DBWebsite), _count(session, DBCriticalPage), _count(session, DBRecipient)) == (0, 0, 0)


# ==========================
#  Adding a critical page
# ==========================


@pytest.fixture
def website_with_a_path(session: Session) -> WebsiteRead:
    """Adds a website that is one part of a bigger website (https://example.com/au)."""
    return WebsiteService(session).create(_new_website(url="https://example.com/au"))


@pytest.mark.anyio
@pytest.mark.parametrize("typed_url", ["/news", "example.com/au/news"])
async def test_a_critical_page_is_added_on_the_website_with_its_baseline(
    session: Session, mocker: MockerFixture, website_with_a_path: WebsiteRead, typed_url: str
) -> None:
    """Tests a critical page typed as a link relative to the website, or without "https://", is added as the full
    URL on that website, and what it looks like now is saved as its baseline."""
    news_page = "https://example.com/au/news"
    news_html = '<html><body><p>Latest news.</p><a href="/au/news/today">Today</a></body></html>'
    _put_online(mocker, _serve({news_page: news_html}))

    added: critical_page_models.CriticalPageRead = await add_critical_page(website_with_a_path.id, typed_url)

    assert str(added.url) == news_page
    saved = critical_page_models.CriticalPageRead.model_validate(session.get(DBCriticalPage, added.id))
    assert saved.website_id == website_with_a_path.id
    assert saved.text_body == news_html
    assert [str(link) for link in saved.links or []] == ["https://example.com/au/news/today"]


@pytest.mark.anyio
async def test_a_critical_page_on_another_website_is_refused(
    session: Session, mocker: MockerFixture, website_with_a_path: WebsiteRead
) -> None:
    """Tests a critical page on a different website is refused before it is loaded, and nothing is added."""
    requested_urls: list[str] = _put_online(mocker, _serve({}))
    pages_before: int = _count(session, DBCriticalPage)

    with pytest.raises(InvalidPageError, match="is not a page on"):
        await add_critical_page(website_with_a_path.id, "https://other.com/news")

    assert requested_urls == []
    assert _count(session, DBCriticalPage) == pages_before


@pytest.mark.anyio
async def test_a_critical_page_that_cannot_be_loaded_is_not_added(
    session: Session, mocker: MockerFixture, website_with_a_path: WebsiteRead
) -> None:
    """Tests a critical page that does not exist is refused with an error naming it, and is not added."""
    _put_online(mocker, _serve({}))
    pages_before: int = _count(session, DBCriticalPage)

    with pytest.raises(PageNotLoadedError) as refused:
        await add_critical_page(website_with_a_path.id, "/missing")

    assert refused.value.url == "https://example.com/au/missing"
    assert _count(session, DBCriticalPage) == pages_before


@pytest.mark.anyio
async def test_a_critical_page_whose_baseline_cannot_be_saved_is_removed_again(
    session: Session, mocker: MockerFixture, website_with_a_path: WebsiteRead
) -> None:
    """Tests a critical page that loads when it is checked, but cannot be loaded again to save its baseline, is
    removed again, so no page is left without a baseline."""
    times_loaded: list[str] = []

    def loads_only_once(request: httpx2.Request) -> httpx2.Response:
        times_loaded.append(str(request.url))
        if len(times_loaded) > 1:
            raise httpx2.ConnectError("Connection refused", request=request)
        return httpx2.Response(200, text="<p>Latest news.</p>")

    _put_online(mocker, loads_only_once)
    pages_before: int = _count(session, DBCriticalPage)

    with pytest.raises(WebConnectionError):
        await add_critical_page(website_with_a_path.id, "/news")

    assert _count(session, DBCriticalPage) == pages_before


@pytest.mark.anyio
async def test_a_critical_page_cannot_be_added_to_a_website_that_does_not_exist(mocker: MockerFixture) -> None:
    """Tests adding a critical page to a website that does not exist (e.g. it was just deleted) is refused."""
    requested_urls: list[str] = _put_online(mocker, _serve({}))

    with pytest.raises(NotFoundError):
        await add_critical_page(uuid.uuid4(), "/news")

    assert requested_urls == []


# ==========================
#  Changing a website's settings
# ==========================


def _last_emailed(session: Session, email: str) -> datetime | None:
    """Looks up when a recipient was last emailed.

    Args:
        session: The test's database session.
        email: The recipient's email address.

    Returns:
        When they were last emailed, or None if they never have been.
    """
    return RecipientService(session).get_by_email(email).last_email_at


@pytest.mark.anyio
async def test_only_added_addresses_are_emailed_and_recorded_as_emailed(
    session: Session,
    bouncing_addresses: set[str],
    email_sender: FakeEmailSender,
    test_website: website_models.WebsiteRead,
) -> None:
    """Tests adding an address emails only that address ("Email address added to website monitoring", naming the
    website) and records it as emailed, while the website's existing recipients are not emailed again."""
    [existing_recipient] = test_website.recipients

    updated: WebsiteRead = await update_website_settings(
        test_website.id, WebsiteSettingsUpdate(add_recipient_emails=["new@example.com"]), email_sender
    )

    assert sorted(recipient.email for recipient in updated.recipients) == sorted(
        [existing_recipient.email, "new@example.com"]
    )
    [email] = email_sender.sent
    assert (email.to, email.subject) == ("new@example.com", RECIPIENT_ADDED_SUBJECT)
    assert str(test_website.url) in email.html_body
    assert _last_emailed(session, "new@example.com") is not None
    assert _last_emailed(session, existing_recipient.email) is None


@pytest.mark.anyio
async def test_an_address_already_on_the_website_is_not_told_again_that_it_was_added(
    session: Session,
    bouncing_addresses: set[str],
    email_sender: FakeEmailSender,
    test_website: website_models.WebsiteRead,
) -> None:
    """Tests adding an address that is already a recipient of the website sends it no "added" email and does not
    record it as emailed, while a new address given at the same time is still emailed once."""
    [existing_recipient] = test_website.recipients

    updated: WebsiteRead = await update_website_settings(
        test_website.id,
        WebsiteSettingsUpdate(add_recipient_emails=[existing_recipient.email, "new@example.com", "new@example.com"]),
        email_sender,
    )

    assert [email.to for email in email_sender.sent] == ["new@example.com"]
    assert sorted(recipient.email for recipient in updated.recipients) == sorted(
        [existing_recipient.email, "new@example.com"]
    )
    assert _last_emailed(session, existing_recipient.email) is None


@pytest.mark.anyio
async def test_nothing_is_changed_when_an_added_address_bounces(
    session: Session,
    bouncing_addresses: set[str],
    email_sender: FakeEmailSender,
    test_website: website_models.WebsiteRead,
) -> None:
    """Tests none of the settings being saved are changed when an address being added bounces."""
    bouncing_addresses.add("bounces@example.com")
    settings = WebsiteSettingsUpdate(days_between_scans=7, add_recipient_emails=["bounces@example.com"])

    with pytest.raises(UndeliverableEmailError):
        await update_website_settings(test_website.id, settings, email_sender)

    saved: WebsiteRead = WebsiteService(session).get(test_website.id)
    assert saved.days_between_scans == test_website.days_between_scans
    assert saved.recipients == test_website.recipients
    assert session.scalars(select(DBRecipient).where(DBRecipient.email == "bounces@example.com")).all() == []


@pytest.mark.anyio
async def test_removing_an_address_and_changing_scan_settings_saves_them(
    session: Session, email_sender: FakeEmailSender, test_website: website_models.WebsiteRead
) -> None:
    """Tests Save Settings saves the scan settings and removes an address, without emailing anyone."""
    [existing_recipient] = test_website.recipients
    settings = WebsiteSettingsUpdate(
        recommended_delay=2.5,
        recommended_concurrent=3,
        days_between_scans=7,
        active=False,
        remove_recipient_emails=[existing_recipient.email],
    )

    await update_website_settings(test_website.id, settings, email_sender)

    saved: WebsiteRead = WebsiteService(session).get(test_website.id)
    assert (saved.recommended_delay, saved.recommended_concurrent, saved.days_between_scans, saved.active) == (
        2.5,
        3,
        7,
        False,
    )
    assert saved.recipients == []
    assert email_sender.sent == []


@pytest.mark.anyio
async def test_changing_the_settings_of_a_website_that_does_not_exist_is_refused(
    bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests changing a website that does not exist is refused before any added address is emailed."""
    with pytest.raises(NotFoundError):
        await update_website_settings(
            uuid.uuid4(), WebsiteSettingsUpdate(add_recipient_emails=["new@example.com"]), email_sender
        )

    assert email_sender.sent == []


# ==========================
#  Deleting a website
# ==========================


def test_deleting_a_website_removes_it_and_its_history(
    session: Session,
    test_website: website_models.WebsiteRead,
    test_critical_page: critical_page_models.CriticalPageRead,
    test_internal_link: internal_link_models.InternalLinkRead,
) -> None:
    """Tests deleting a website also removes its saved history: its scans and what they found, its critical pages
    and its internal links."""
    session.add(
        DBScanRun(
            website_id=test_website.id,
            scanned_at=datetime.now(UTC),
            status=ScanStatus.SUCCESS,
            changes=[DBChange(position=0, kind=ChangeKind.INTERNAL_LINK_ADDED, url=f"{test_website.url}new-page")],
        )
    )
    session.flush()

    delete_website(test_website.id)

    session.expire_all()
    assert session.get(DBWebsite, test_website.id) is None
    for table in (DBCriticalPage, DBInternalLink, DBScanRun):
        assert session.scalars(select(table).where(table.website_id == test_website.id)).all() == []
    assert session.scalars(select(DBChange)).all() == []


def test_deleting_a_website_that_does_not_exist_is_refused(session: Session) -> None:
    """Tests deleting a website that does not exist (e.g. already deleted in another window) is refused."""
    with pytest.raises(NotFoundError):
        delete_website(uuid.uuid4())
