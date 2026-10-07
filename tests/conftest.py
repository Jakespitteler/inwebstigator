import sqlite3

from sqlalchemy.pool import ConnectionPoolEntry

from app.core.config import config

config.automatic_scans = False

import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import UUID as PG_UUID
from sqlalchemy import Connection, DateTime, Engine, MetaData, StaticPool, String, create_engine, event, text
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column
from tenacity import wait_none

from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.email_service.sender import EmailSender, get_email_sender
from app.backend.links import normalise_url
from app.backend.page_fetcher import fetch_content_from_url
from app.db import repository, schema
from app.db.session import get_db_session
from app.main import app
from app.models import critical_page_models, internal_link_models, recipient_models, website_models
from app.models.field_types import URLString
from tests.fakes import FakeEmailSender

type RequestHandler = Callable[[httpx2.Request], httpx2.Response]


# ==========================
#  Database & Schema Setup
# ==========================

test_metadata = MetaData()
TestBase = declarative_base(metadata=test_metadata)


class DBTestTable(TestBase):
    __tablename__: str = "test_table"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))


def _disable_driver_transactions(dbapi_connection: sqlite3.Connection, _connection_record: ConnectionPoolEntry) -> None:
    """Stops Python's sqlite3 driver starting and ending transactions on its own."""
    dbapi_connection.isolation_level = None


def _begin_transaction(connection: Connection) -> None:
    """Starts a real transaction whenever SQLAlchemy begins one, so SAVEPOINTs nest inside it."""
    connection.exec_driver_sql("BEGIN")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Creates a database engine for the test session."""
    test_engine = create_engine(
        config.test_db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Python's sqlite3 driver starts and ends transactions on its own, which breaks SAVEPOINTs:
    # an app-level commit inside a test then commits for real and leaks into later tests.
    # Hand transaction control to SQLAlchemy instead (the fix from SQLAlchemy's SQLite docs).
    event.listen(test_engine, "connect", _disable_driver_transactions)
    event.listen(test_engine, "begin", _begin_transaction)

    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def setup_database(engine: Engine) -> None:
    """Creates the database schema."""
    schema.Base.metadata.create_all(bind=engine)
    TestBase.metadata.create_all(bind=engine)


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """
    Provides a transactional database session for testing.
    Uses SAVEPOINTs so app-level commits don't break test isolation.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        db_session = Session(bind=connection, join_transaction_mode="create_savepoint")

        yield db_session

        db_session.close()
        transaction.rollback()


@pytest.fixture()
def api_client(session: Session) -> Iterator[TestClient]:
    """
    Creates a FastAPI test client with the database session dependency overridden.

    Args:
        session: The database session fixture to be injected.

    Yields:
        The configured TestClient instance.
    """
    app.dependency_overrides[get_db_session] = lambda: session
    with TestClient(app) as client:
        yield client
        app.dependency_overrides.clear()


def _skip_confirmation(email: OutgoingEmail, email_sender: EmailSender) -> None:
    """Stands in for confirming an address can receive email, so tests do not send emails or wait for bounces.

    Args:
        email: The confirmation email that would have been sent.
        email_sender: The sender that would have sent it.
    """


@pytest.fixture(autouse=True)
def skip_email_confirmations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treats every added email address as able to receive email, so tests do not send confirmation emails."""
    monkeypatch.setattr("app.frontend.api.routers.confirm_address_can_receive_email", _skip_confirmation)
    monkeypatch.setattr("app.frontend.api.routers.send_confirmation", _skip_confirmation)


@pytest.fixture(autouse=True)
def email_sender(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEmailSender]:
    """Replaces the mail server with a fake for every test, so no test can send a real email.

    Yields:
        The fake sender, holding every email the test sent.
    """
    fake_sender = FakeEmailSender()
    monkeypatch.setattr("app.scanner.get_email_sender", lambda: fake_sender)
    monkeypatch.setattr("app.scheduler.get_email_sender", lambda: fake_sender)
    app.dependency_overrides[get_email_sender] = lambda: fake_sender
    yield fake_sender
    app.dependency_overrides.pop(get_email_sender, None)


@pytest.fixture(autouse=True)
def disable_retry_wait() -> Iterator[None]:
    """Stops fetching pages waiting between retries, so tests of failures run quickly."""
    fetch_content_from_url.retry.wait = wait_none()  # pyright: ignore[reportFunctionMemberAccess]
    yield


# ==========================
#  Test Records
# ==========================


@pytest.fixture
def test_url() -> URLString:
    """Provides a standard test URL matching test_website domain."""
    return normalise_url("https://www.test_website.com/")


def _create_and_add[DBRecord: schema.Base](session: Session, record: DBRecord) -> DBRecord:
    """
    Creates a temporary record for testing.

    Args:
        session: The database session fixture.
        record: The record to add to the database.

    Returns:
        The created record.
    """
    repository.add(session, record)
    assert record.id is not None
    return record


@pytest.fixture()
def test_record(session: Session) -> DBTestTable:
    return _create_and_add(session, DBTestTable(name="Test Record"))


@pytest.fixture()
def test_recipient(session: Session) -> recipient_models.RecipientRead:
    recipient = recipient_models.RecipientRead.model_validate(
        _create_and_add(
            session,
            record=schema.DBRecipient(email="testRecipient@gmail.com"),
        )
    )
    return recipient


@pytest.fixture()
def test_website(
    session: Session, test_recipient: recipient_models.RecipientRead, test_url: URLString
) -> website_models.WebsiteRead:
    return website_models.WebsiteRead.model_validate(
        _create_and_add(
            session,
            record=schema.DBWebsite(
                url=test_url,
                recipients=[repository.get(session, table=schema.DBRecipient, id=test_recipient.id)],
                recommended_delay=0,
                recommended_concurrent=20,
            ),
        )
    )


@pytest.fixture()
def test_critical_page(session: Session, test_website: schema.DBWebsite) -> critical_page_models.CriticalPageRead:
    return critical_page_models.CriticalPageRead.model_validate(
        _create_and_add(
            session,
            record=schema.DBCriticalPage(
                url=f"{test_website.url}test_critical_page",
                website_id=test_website.id,
            ),
        )
    )


@pytest.fixture()
def test_internal_link(session: Session, test_website: schema.DBWebsite) -> internal_link_models.InternalLinkRead:
    return internal_link_models.InternalLinkRead.model_validate(
        _create_and_add(
            session,
            record=schema.DBInternalLink(url=f"{test_website.url}test_internal_link", website_id=test_website.id),
        )
    )


# ======================================
# Web Data Fixtures
# ======================================


@pytest.fixture
def test_html_content(test_url: URLString) -> str:
    """Provides a mock HTML string containing various link structures."""
    return f"""
    <html>
        <body>
            <a href="/about">About Us</a>
            <a href="{test_url}contact">Contact</a>
            <a href="https://external.com/page">External Site</a>
            <a href="/page1.html">Page 1</a>
            <a href="/404-page.html">Dead</a>
        </body>
    </html>
    """


# ======================================
# Web Client Factory Fixtures
# ======================================


@pytest.fixture
def mock_client_factory(test_url: URLString) -> Callable[[RequestHandler], httpx2.AsyncClient]:
    """Fixture factory to easily create an AsyncClient with a MockTransport."""

    def _create_client(handler: RequestHandler, base_url: str = test_url) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler), base_url=base_url)

    return _create_client


# ======================================
# Web Request Handlers
# ======================================


@pytest.fixture
def website_handler(test_url: str, test_html_content: str) -> RequestHandler:
    """Provides a mock request handler simulating a multi-page website."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        url: str = str(request.url)
        if url == test_url:
            return httpx2.Response(200, text=test_html_content)
        elif url == f"{test_url}test_critical_page":
            return httpx2.Response(
                200,
                text="""
                <html>
                    <body>
                        <p>Updated Critical Page Content</p>
                        <a href="/about">About Us</a>
                        <a href="/new-link">New Link</a>
                    </body>
                </html>
                """,
            )
        elif url == f"{test_url}page1.html":
            return httpx2.Response(200, text='<a href="/page2.html">Page 2</a> <a href="/">Home</a>')
        elif url == f"{test_url}page2.html":
            return httpx2.Response(200, text="<p>End of line</p>")
        elif url == f"{test_url}404-page.html":
            return httpx2.Response(404, text="Not Found")
        return httpx2.Response(404)

    return handler


@pytest.fixture
def redirect_handler(test_url: URLString) -> RequestHandler:
    """Provides a mock request handler simulating an HTTP redirect."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == f"{test_url}initial":
            return httpx2.Response(301, headers={"Location": f"{test_url}final"})
        return httpx2.Response(200, text="Final Destination Content")

    return handler


# ======================================
# Web Error & Exception Request Handlers
# ======================================


@pytest.fixture
def rate_limit_handler() -> RequestHandler:
    """Provides a mock request handler simulating a rate limit (429 Too Many Requests)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, text="Too Many Requests")

    return handler


@pytest.fixture
def connection_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx2 ConnectError."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Mocked Connection Error", request=request)

    return handler


@pytest.fixture
def timeout_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx2 TimeoutException."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("Mocked Timeout Exception")

    return handler


@pytest.fixture
def server_error_handler() -> RequestHandler:
    """Provides a mock request handler simulating an internal server error (500)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="Internal Server Error")

    return handler


@pytest.fixture
def request_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx2 RequestError."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RequestError("Protocol Error", request=request)

    return handler


@pytest.fixture
def unexpected_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises a generic RuntimeError."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("Unexpected failure")

    return handler


# ======================================
# Web Utility & Timing Request Handlers
# ======================================


@pytest.fixture
def timed_handler(test_url: str) -> tuple[RequestHandler, list[float]]:
    """Provides a mock request handler and a list tracking request timestamps for delay tests."""
    request_timestamps: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        request_timestamps.append(time.monotonic())
        url: str = str(request.url)
        if url == test_url:
            return httpx2.Response(200, text='<a href="/page1.html">Page 1</a>')
        return httpx2.Response(200, text="<p>End</p>")

    return handler, request_timestamps
