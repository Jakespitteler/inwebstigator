import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import UUID as PG_UUID
from sqlalchemy import DateTime, Engine, MetaData, StaticPool, String, create_engine, text
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column

from app.core.config import config
from app.db import core, repository, schema
from app.main import app

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


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Creates a database engine for the test session."""
    test_engine = create_engine(
        config.test_db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def setup_database(engine: Engine) -> None:
    """Creates the database schema."""
    core.Base.metadata.create_all(bind=engine)
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
    app.dependency_overrides[core.get_db_session] = lambda: session
    with TestClient(app) as client:
        yield client
        app.dependency_overrides.clear()


# ==========================
#  Test Records
# ==========================


def _create_and_add[DBRecord: core.Base](session: Session, record: DBRecord) -> DBRecord:
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
def test_website(session: Session) -> schema.DBWebsite:
    return _create_and_add(
        session,
        record=schema.DBWebsite(url="https://www.test_website.com"),
    )


@pytest.fixture()
def test_critical_page(session: Session, test_website: schema.DBWebsite) -> schema.DBCriticalPage:
    return _create_and_add(
        session,
        record=schema.DBCriticalPage(
            url=f"{test_website.url}/test_critical_page",
            links=[],
            documents=[],
            text_body="",
            website_id=test_website.id,
        ),
    )


@pytest.fixture()
def test_internal_link(session: Session, test_website: schema.DBWebsite) -> schema.DBInternalLink:
    return _create_and_add(
        session,
        record=schema.DBInternalLink(url=f"{test_website.url}/test_internal_link", website_id=test_website.id),
    )


# ======================================
# Web Data Fixtures
# ======================================


@pytest.fixture
def test_url() -> str:
    """Provides a standard test URL fixture."""
    return "https://example.com/"


@pytest.fixture
def test_html_content() -> str:
    """Provides a mock HTML string containing various link structures."""
    return """
    <html>
        <body>
            <a href="/about">About Us</a>
            <a href="https://example.com/contact">Contact</a>
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
def mock_client_factory() -> Callable[[RequestHandler], httpx2.AsyncClient]:
    """Fixture factory to easily create an AsyncClient with a MockTransport."""

    def _create_client(handler: RequestHandler, base_url: str = "https://mocksite.com") -> httpx2.AsyncClient:
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
        elif url == f"{test_url}page1.html":
            return httpx2.Response(200, text='<a href="/page2.html">Page 2</a> <a href="/">Home</a>')
        elif url == f"{test_url}page2.html":
            return httpx2.Response(200, text="<p>End of line</p>")
        elif url == f"{test_url}404-page.html":
            return httpx2.Response(404, text="Not Found")
        return httpx2.Response(404)

    return handler


@pytest.fixture
def redirect_handler() -> RequestHandler:
    """Provides a mock request handler simulating an HTTP redirect."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == "https://example.com/initial":
            return httpx2.Response(301, headers={"Location": "https://example.com/final"})
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
