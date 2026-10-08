import imaplib
import smtplib
import ssl
from dataclasses import replace
from types import TracebackType
from typing import Self

import pytest
from pydantic import SecretStr

from app.backend.email_service import delivery
from app.backend.email_service.delivery import confirm_address_can_receive_email
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.email_service.settings import InboxSettings
from app.core.errors import UndeliverableEmailError
from tests.fakes import FakeEmailSender

INBOX_SETTINGS = InboxSettings(
    host="imap.example.com",
    port=993,
    timeout_seconds=30,
    username="sender@example.com",
    password=SecretStr("app-password"),
    bounce_wait_seconds=6,
    bounce_poll_seconds=3,
)


def confirmation_to(address: str) -> OutgoingEmail:
    return OutgoingEmail(to=address, subject="Subject", html_body="<p>Hi</p>")


class FakeInbox:
    """Stands in for the inbox connection, so tests do not log in to a real mail account."""

    def __init__(self, bounce_counts: list[int]) -> None:
        self.bounce_counts: list[int] = list(bounce_counts)
        self.searches: list[str] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def select(self, mailbox: str, readonly: bool) -> None:
        return None

    def search(self, charset: str | None, criteria: str) -> tuple[str, list[bytes]]:
        self.searches.append(criteria)
        count: int = self.bounce_counts.pop(0)
        return "OK", [b" ".join(str(number).encode() for number in range(1, count + 1))]


def _no_wait(seconds: float) -> None:
    """Stands in for time.sleep, so the tests do not wait between inbox checks."""


@pytest.fixture(autouse=True)
def skip_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the waits between inbox checks, so the tests run quickly."""
    monkeypatch.setattr("app.backend.email_service.delivery.time.sleep", _no_wait)


def use_inbox(monkeypatch: pytest.MonkeyPatch, inbox: FakeInbox | None) -> None:
    """Makes the inbox open as the given fake, or fail to open if it is None."""

    def open_inbox(settings: InboxSettings) -> FakeInbox | None:
        return inbox

    monkeypatch.setattr("app.backend.email_service.delivery._open_inbox", open_inbox)


def test_confirm_address_passes_when_nothing_bounces(monkeypatch: pytest.MonkeyPatch) -> None:
    use_inbox(monkeypatch, FakeInbox([0, 0, 0]))
    sender = FakeEmailSender()

    confirm_address_can_receive_email(confirmation_to("real@example.com"), sender, INBOX_SETTINGS)

    assert [email.to for email in sender.sent] == ["real@example.com"]


def test_confirm_address_raises_when_the_email_bounces(monkeypatch: pytest.MonkeyPatch) -> None:
    use_inbox(monkeypatch, FakeInbox([1, 1, 2]))
    sender = FakeEmailSender()

    with pytest.raises(UndeliverableEmailError) as error:
        confirm_address_can_receive_email(confirmation_to("fake@example.com"), sender, INBOX_SETTINGS)

    assert error.value.address == "fake@example.com"
    assert len(sender.sent) == 1


def test_confirm_address_ignores_bounces_from_before_the_email_was_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    use_inbox(monkeypatch, FakeInbox([3, 3, 3]))
    sender = FakeEmailSender()

    confirm_address_can_receive_email(confirmation_to("real@example.com"), sender, INBOX_SETTINGS)

    assert len(sender.sent) == 1


def test_confirm_address_raises_when_the_server_refuses_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    class RefusingSender(FakeEmailSender):
        def send(self, email: OutgoingEmail) -> None:
            raise smtplib.SMTPRecipientsRefused({email.to: (550, b"No such user")})

    use_inbox(monkeypatch, FakeInbox([0]))

    with pytest.raises(UndeliverableEmailError):
        confirm_address_can_receive_email(confirmation_to("fake@example.com"), RefusingSender(), INBOX_SETTINGS)


def test_confirm_address_still_sends_when_the_inbox_cannot_be_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    use_inbox(monkeypatch, None)
    sender = FakeEmailSender()

    confirm_address_can_receive_email(confirmation_to("real@example.com"), sender, INBOX_SETTINGS)

    assert len(sender.sent) == 1


def test_bounces_from_gmail_and_microsoft_are_both_searched_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests Microsoft 365 and Outlook bounces (from "postmaster") are found as well as Gmail's ("mailer-daemon")."""
    inbox = FakeInbox([0, 0, 0])
    use_inbox(monkeypatch, inbox)

    confirm_address_can_receive_email(confirmation_to("real@example.com"), FakeEmailSender(), INBOX_SETTINGS)

    assert inbox.searches[0] == '((OR FROM "mailer-daemon" FROM "postmaster") TEXT "real@example.com")'


def test_open_inbox_checks_the_servers_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[ssl.SSLContext] = []

    class FakeIMAP:
        def __init__(self, host: str, port: int, ssl_context: ssl.SSLContext, timeout: float) -> None:
            opened.append(ssl_context)

        def login(self, user: str, password: str) -> None:
            return None

    monkeypatch.setattr("app.backend.email_service.delivery.imaplib.IMAP4_SSL", FakeIMAP)

    assert delivery._open_inbox(INBOX_SETTINGS) is not None  # pyright: ignore[reportPrivateUsage]
    [context] = opened
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_open_inbox_gives_up_without_an_inbox_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("The inbox should not be opened without a server")

    monkeypatch.setattr("app.backend.email_service.delivery.imaplib.IMAP4_SSL", fail_if_opened)

    assert delivery._open_inbox(replace(INBOX_SETTINGS, host=None)) is None  # pyright: ignore[reportPrivateUsage]


def test_open_inbox_returns_nothing_when_the_login_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class RefusingIMAP:
        def __init__(self, host: str, port: int, ssl_context: ssl.SSLContext, timeout: float) -> None:
            return None

        def login(self, user: str, password: str) -> None:
            raise imaplib.IMAP4.error("Invalid credentials")

    monkeypatch.setattr("app.backend.email_service.delivery.imaplib.IMAP4_SSL", RefusingIMAP)

    assert delivery._open_inbox(INBOX_SETTINGS) is None  # pyright: ignore[reportPrivateUsage]


def test_confirm_address_still_sends_when_the_inbox_server_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests an inbox server that cannot be reached (e.g. no internet to it, or a wrong IMAP_HOST) does not stop the
    email being sent, as the bounce check is only a bonus."""

    def refuse_connection(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr("app.backend.email_service.delivery.imaplib.IMAP4_SSL", refuse_connection)
    sender = FakeEmailSender()

    confirm_address_can_receive_email(confirmation_to("real@example.com"), sender, INBOX_SETTINGS)

    assert [email.to for email in sender.sent] == ["real@example.com"]


def test_open_inbox_logs_in_to_the_configured_server_as_the_sending_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the inbox opened is the configured server and port, logged in with the sending account's password."""
    opened: list[tuple[str, int, float]] = []
    logins: list[tuple[str, str]] = []

    class RecordingIMAP:
        """Records where the inbox is opened and who logs in."""

        def __init__(self, host: str, port: int, ssl_context: ssl.SSLContext, timeout: float) -> None:
            opened.append((host, port, timeout))

        def login(self, user: str, password: str) -> None:
            logins.append((user, password))

    monkeypatch.setattr("app.backend.email_service.delivery.imaplib.IMAP4_SSL", RecordingIMAP)

    delivery._open_inbox(INBOX_SETTINGS)  # pyright: ignore[reportPrivateUsage]

    assert opened == [("imap.example.com", 993, 30)]
    assert logins == [("sender@example.com", "app-password")]


def test_confirm_address_watches_the_inbox_only_for_the_configured_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the inbox is watched for EMAIL_BOUNCE_WAIT_SECONDS, checking every EMAIL_BOUNCE_POLL_SECONDS, and a bounce
    that arrives after that is not seen."""
    waits: list[float] = []
    monkeypatch.setattr("app.backend.email_service.delivery.time.sleep", waits.append)
    use_inbox(monkeypatch, FakeInbox([0, 0, 0, 1]))

    confirm_address_can_receive_email(confirmation_to("slow@example.com"), FakeEmailSender(), INBOX_SETTINGS)

    assert waits == [3, 3]
