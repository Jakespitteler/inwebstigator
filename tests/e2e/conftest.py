"""Browser tests: drive the real dashboard in a browser, against the real app running in the background.

The app runs on a local port with its own throwaway database. Websites are fake (served from `FakeWebsites`
rather than the internet) and emails are recorded in `SentEmails` rather than sent, so nothing leaves the
computer. Every test starts with an empty database and no fake websites.

Run them with `uv run pytest tests/e2e` (or `uv run pytest -m e2e`); skip them with `-m "not e2e"`.
They use Google Chrome or Microsoft Edge if installed, otherwise Playwright's own Chromium
(`uv run playwright install chromium`), and are skipped if no browser can be started.
"""

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from playwright.sync_api import BrowserType, ConsoleMessage, Page, Route
from playwright.sync_api import Error as PlaywrightError
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, selectinload

from app.db import core
from app.db.schema import Base, DBCriticalPage, DBRecipient, DBWebsite
from app.main import app

E2E_DIRECTORY: Path = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Marks every test in this folder as `e2e`, and runs them after all the other tests.

    Playwright keeps an event loop running from the first browser test until the end of the test run,
    so async tests run after it would fail with "Cannot run the event loop while another loop is running".
    """
    for item in items:
        if E2E_DIRECTORY in item.path.parents:
            item.add_marker(pytest.mark.e2e)
    items.sort(key=lambda item: E2E_DIRECTORY in item.path.parents)


# ======================================
# Browser
# ======================================


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any], browser_type: BrowserType) -> dict[str, Any]:
    """Picks a browser that can start: Google Chrome, then Microsoft Edge, then Playwright's own Chromium.

    Playwright's Chromium is not supported on every OS (e.g. macOS 12), but an installed Chrome or Edge is.
    A browser chosen on the command line (`--browser` or `--browser-channel`) is used as it is.
    """
    if browser_type_launch_args.get("channel") or browser_type.name != "chromium":
        return browser_type_launch_args

    for channel in ("chrome", "msedge", None):
        launch_args = {**browser_type_launch_args, **({"channel": channel} if channel else {})}
        try:
            browser_type.launch(**launch_args).close()
        except PlaywrightError:
            continue
        return launch_args

    pytest.skip(
        "No browser for the dashboard tests: install Google Chrome or Microsoft Edge, "
        "or run `uv run playwright install chromium`."
    )


# ======================================
# Fake websites and emails
# ======================================


def _without_trailing_slash(url: str) -> str:
    return url.rstrip("/")


@dataclass
class FakeWebsites:
    """Websites the app scans instead of real ones. A page not set here returns a 404.

    Attributes:
        pages: Each page's HTML, by URL.
        delay_seconds: How long every page takes to load, e.g. to test cancelling a scan.
    """

    pages: dict[str, str] = field(default_factory=dict[str, str])
    delay_seconds: float = 0

    def set_page(self, url: str, html: str) -> None:
        """Adds or changes a page."""
        self.pages[_without_trailing_slash(url)] = html

    def reset(self) -> None:
        self.pages.clear()
        self.delay_seconds = 0

    async def respond(self, request: httpx2.Request) -> httpx2.Response:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        html: str | None = self.pages.get(_without_trailing_slash(str(request.url)))
        if html is None:
            return httpx2.Response(404, text="Not found", request=request)
        return httpx2.Response(200, text=html, headers={"Content-Type": "text/html"}, request=request)

    def client(self, **kwargs: Any) -> httpx2.AsyncClient:
        """Returns a client that loads these fake pages, in place of the app's `AsyncClient()`."""
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.respond))


@dataclass
class SentEmail:
    to: str
    subject: str


@dataclass
class SentEmails:
    """Emails the app would have sent, recorded instead of sending them."""

    emails: list[SentEmail] = field(default_factory=list[SentEmail])

    def record_message(self, msg: EmailMessage) -> None:
        self.emails.append(SentEmail(to=str(msg["To"]), subject=str(msg["Subject"])))

    def record_confirmation(self, address: str, subject: str, html_body: str) -> None:
        self.emails.append(SentEmail(to=address, subject=subject))

    def reset(self) -> None:
        self.emails.clear()


# ======================================
# The app, running in the background
# ======================================


@dataclass
class RunningApp:
    """The app running in the background for the browser tests.

    Attributes:
        url: The dashboard's address.
        engine: The throwaway database.
        websites: The fake websites it scans.
        sent_emails: The emails it would have sent.
    """

    url: str
    engine: Engine
    websites: FakeWebsites
    sent_emails: SentEmails

    def session(self) -> Session:
        """Opens a session on the app's database, e.g. to set up or check saved state."""
        return Session(self.engine)

    def add_website(
        self, url: str, critical_pages: Sequence[str] = (), recipients: Sequence[str] = (), **fields: Any
    ) -> None:
        """Saves a website straight to the database, as if it had been added and scanned before.

        Like the app, its main URL is always one of its critical pages.

        Args:
            url: The website's address.
            critical_pages: Its other critical pages.
            recipients: Its notification emails.
            fields: Other columns to set, e.g. `active=False`.
        """
        with self.session() as session:
            website = DBWebsite(url=url, last_scan_at=datetime.now(), **fields)
            website.critical_pages = [DBCriticalPage(url=page_url) for page_url in (url, *critical_pages)]
            website.recipients = [DBRecipient(email=email) for email in recipients]
            session.add(website)
            session.commit()

    def saved_websites(self) -> list[DBWebsite]:
        """Returns the websites saved in the database, with their critical pages and recipients loaded."""
        with self.session() as session:
            websites = session.scalars(
                select(DBWebsite).options(selectinload(DBWebsite.critical_pages), selectinload(DBWebsite.recipients))
            ).all()
            session.expunge_all()
            return list(websites)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="e2e-server", daemon=True)
    thread.start()

    deadline: float = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("The app did not start for the browser tests.")
        time.sleep(0.05)
    return server, thread


@pytest.fixture(scope="package")
def running_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningApp]:
    """Starts the app in the background once for all the browser tests, with fakes for everything outside it."""
    engine: Engine = create_engine(
        f"sqlite:///{tmp_path_factory.mktemp('e2e') / 'inwebstigator.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    websites, sent_emails = FakeWebsites(), SentEmails()

    with pytest.MonkeyPatch.context() as monkeypatch:
        # Every database session the app opens (requests, scans) uses the throwaway database
        original_engine = core.SessionLocal.kw["bind"]
        core.SessionLocal.configure(bind=engine)

        monkeypatch.setattr("app.scanner.AsyncClient", websites.client)
        monkeypatch.setattr("app.frontend.api.routers.AsyncClient", websites.client)
        monkeypatch.setattr("app.scanner.send_email", sent_emails.record_message)
        # Confirmation emails are recorded by `skip_email_confirmations` below, which runs for every test

        server, thread = _start_server(_free_port())
        try:
            yield RunningApp(
                url=f"http://127.0.0.1:{server.config.port}/",
                engine=engine,
                websites=websites,
                sent_emails=sent_emails,
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            core.SessionLocal.configure(bind=original_engine)
            engine.dispose()


@pytest.fixture(autouse=True)
def skip_email_confirmations(monkeypatch: pytest.MonkeyPatch, running_app: RunningApp) -> None:
    """Records the confirmation emails sent when an email address is added, instead of sending them.

    Replaces the fixture of the same name in tests/conftest.py, which throws them away, for the browser tests.
    """
    record = running_app.sent_emails.record_confirmation
    monkeypatch.setattr("app.frontend.api.routers.confirm_address_can_receive_email", record)
    monkeypatch.setattr("app.frontend.api.routers.send_confirmation", record)


@pytest.fixture
def app_server(running_app: RunningApp) -> RunningApp:
    """The running app, with an empty database, no fake websites and no sent emails."""
    with running_app.engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    running_app.websites.reset()
    running_app.sent_emails.reset()
    return running_app


# ======================================
# The dashboard in the browser
# ======================================


@dataclass
class BrowserErrors:
    """Errors the page reported: uncaught script errors and errors logged to the console."""

    messages: list[str] = field(default_factory=list[str])


def _serve_external_requests_locally(route: Route) -> None:
    """Answers requests outside the app (e.g. Google Fonts) with nothing, so tests never need the internet."""
    route.fulfill(status=200, body="")


@pytest.fixture
def browser_errors(page: Page, app_server: RunningApp) -> Iterator[BrowserErrors]:
    """Records the page's script and console errors, and fails the test if there were any."""
    errors = BrowserErrors()

    def record_console_message(message: ConsoleMessage) -> None:
        # The browser logs every failed request (e.g. a 422 for a page that can't be loaded) as a console error,
        # even when the dashboard handles it, so only errors the page itself logs are counted
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            errors.messages.append(f"Console error: {message.text}")

    page.on("pageerror", lambda error: errors.messages.append(f"Script error: {error}\n{error.stack}"))
    page.on("console", record_console_message)
    page.route(lambda url: not url.startswith(app_server.url), _serve_external_requests_locally)

    yield errors

    assert errors.messages == [], "The dashboard reported errors:\n" + "\n".join(errors.messages)


@pytest.fixture
def open_dashboard(page: Page, app_server: RunningApp, browser_errors: BrowserErrors) -> Callable[[str], Page]:
    """Returns a function that opens the dashboard (optionally at a path, e.g. "#updates") and waits for it."""

    def open_at(path: str = "") -> Page:
        page.goto(app_server.url + path)
        page.wait_for_load_state("load")
        return page

    return open_at
