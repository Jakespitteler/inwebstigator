import smtplib
import ssl
import threading
from dataclasses import replace
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import pytest
from pydantic import SecretStr

from app.backend.email_service.delivery import (
    SmtpEmailSender,
    get_email_sender,
    is_temporary_failure,
    is_undeliverable,
    send_confirmation,
)
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.email_service.settings import RetrySettings, SmtpSettings
from app.core.config import config
from app.core.errors import UndeliverableEmailError
from tests.fakes import FakeEmailSender

EMAIL = OutgoingEmail(to="recipient@example.com", subject="Subject", html_body="<p>Hello</p>")
NO_WAIT_RETRIES = RetrySettings(max_attempts=3, min_wait_seconds=0, max_wait_seconds=0, multiplier=0)


def smtp_settings(port: int = 465, username: str = "user@example.com") -> SmtpSettings:
    """Builds settings for a test mail server that never waits between retries."""
    return SmtpSettings(
        host="smtp.example.com",
        port=port,
        timeout_seconds=30,
        username=username,
        password=SecretStr("app-password"),
        sender_address="sender@example.com",
        retry=NO_WAIT_RETRIES,
    )


class FakeSMTP:
    """Stands in for a connection to the mail server, recording what the sender does with it."""

    connections: list["FakeSMTP"] = []
    send_failures: list[BaseException] = []

    def __init__(self, host: str, port: int, timeout: float, context: ssl.SSLContext | None = None) -> None:
        self.host: str = host
        self.port: int = port
        self.timeout: float = timeout
        self.context: ssl.SSLContext | None = context
        self.starttls_context: ssl.SSLContext | None = None
        self.logged_in: tuple[str, str] | None = None
        self.sent_messages: list[EmailMessage] = []
        self.events: list[str] = []
        self.closed: bool = False
        FakeSMTP.connections.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def starttls(self, context: ssl.SSLContext) -> None:
        self.events.append("starttls")
        self.starttls_context = context

    def login(self, user: str, password: str) -> None:
        self.events.append("login")
        self.logged_in = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        if FakeSMTP.send_failures:
            raise FakeSMTP.send_failures.pop(0)
        self.sent_messages.append(msg)

    def quit(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_mail_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces both kinds of SMTP connection with the fake, and clears what earlier tests recorded."""
    FakeSMTP.connections = []
    FakeSMTP.send_failures = []
    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP", FakeSMTP)


def assert_verifies_certificates(context: ssl.SSLContext | None) -> None:
    """Checks a TLS context checks the server's certificate and name, so the password can't go to an impostor."""
    assert context is not None
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_send_uses_an_encrypted_connection_that_checks_the_certificate_on_port_465() -> None:
    SmtpEmailSender(smtp_settings(port=465)).send(EMAIL)

    [connection] = FakeSMTP.connections
    assert (connection.host, connection.port, connection.timeout) == ("smtp.example.com", 465, 30)
    assert_verifies_certificates(connection.context)
    assert connection.starttls_context is None
    assert connection.logged_in == ("user@example.com", "app-password")
    assert [message["To"] for message in connection.sent_messages] == ["recipient@example.com"]
    assert connection.closed


def test_send_upgrades_with_starttls_on_port_587() -> None:
    """Tests port 587 (the only option on Microsoft 365) works, upgrading the connection before logging in."""
    SmtpEmailSender(smtp_settings(port=587)).send(EMAIL)

    [connection] = FakeSMTP.connections
    assert connection.context is None
    assert_verifies_certificates(connection.starttls_context)
    assert connection.events[:2] == ["starttls", "login"]
    assert len(connection.sent_messages) == 1


def test_send_does_not_log_in_without_a_username() -> None:
    SmtpEmailSender(smtp_settings(username="")).send(EMAIL)

    assert FakeSMTP.connections[0].logged_in is None


def test_send_retries_a_temporary_failure_until_the_tries_run_out() -> None:
    FakeSMTP.send_failures = [TimeoutError("Connection timed out")] * 3

    with pytest.raises(TimeoutError, match="Connection timed out"):
        SmtpEmailSender(smtp_settings()).send(EMAIL)

    assert len(FakeSMTP.connections) == 3


def test_send_reconnects_after_a_dropped_connection() -> None:
    FakeSMTP.send_failures = [smtplib.SMTPServerDisconnected("Connection unexpectedly closed")]

    SmtpEmailSender(smtp_settings()).send(EMAIL)

    assert len(FakeSMTP.connections) == 2
    assert len(FakeSMTP.connections[1].sent_messages) == 1


@pytest.mark.parametrize(
    "permanent_error",
    [
        smtplib.SMTPAuthenticationError(535, b"Bad credentials"),
        smtplib.SMTPRecipientsRefused({"recipient@example.com": (550, b"No such user")}),
        smtplib.SMTPDataError(554, b"Rejected as spam"),
    ],
)
def test_send_does_not_retry_a_permanent_failure(permanent_error: smtplib.SMTPException) -> None:
    FakeSMTP.send_failures = [permanent_error]

    with pytest.raises(type(permanent_error)):
        SmtpEmailSender(smtp_settings()).send(EMAIL)

    assert len(FakeSMTP.connections) == 1


@pytest.mark.parametrize(
    ("error", "is_temporary"),
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (smtplib.SMTPServerDisconnected(), True),
        (smtplib.SMTPDataError(451, b"Try again later"), True),
        (smtplib.SMTPDataError(552, b"Message too large"), False),
        (smtplib.SMTPSenderRefused(451, b"Busy", "sender@example.com"), False),
        (ValueError(), False),
    ],
)
def test_is_temporary_failure(error: BaseException, is_temporary: bool) -> None:
    assert is_temporary_failure(error) is is_temporary


def test_sender_keeps_one_connection_open_for_every_email_in_a_with_block() -> None:
    """Tests a run that emails many recipients logs in once, and the connection is closed afterwards."""
    sender = SmtpEmailSender(smtp_settings())
    with sender:
        sender.send(EMAIL)
        sender.send(EMAIL)
        assert not FakeSMTP.connections[0].closed

    [connection] = FakeSMTP.connections
    assert len(connection.sent_messages) == 2
    assert connection.closed


def test_send_confirmation_reports_a_refused_address_as_undeliverable() -> None:
    class RefusingSender(FakeEmailSender):
        def send(self, email: OutgoingEmail) -> None:
            raise smtplib.SMTPRecipientsRefused({email.to: (550, b"No such user")})

    with pytest.raises(UndeliverableEmailError) as error:
        send_confirmation(EMAIL, RefusingSender())

    assert error.value.address == "recipient@example.com"


def test_send_confirmation_sends_the_email() -> None:
    fake_sender = FakeEmailSender()

    send_confirmation(EMAIL, fake_sender)

    assert fake_sender.sent == [EMAIL]


@pytest.mark.parametrize(
    ("error", "will_always_fail"),
    [
        (smtplib.SMTPRecipientsRefused({"recipient@example.com": (550, b"No such user")}), True),
        (smtplib.SMTPRecipientsRefused({"recipient@example.com": (450, b"Greylisted, try again later")}), False),
        (smtplib.SMTPDataError(554, b"Rejected as spam"), True),
        (smtplib.SMTPDataError(552, b"Message too large"), True),
        (smtplib.SMTPDataError(451, b"Try again later"), False),
        (smtplib.SMTPAuthenticationError(535, b"Bad credentials"), False),
        (smtplib.SMTPSenderRefused(553, b"Sender not allowed", "sender@example.com"), False),
        (smtplib.SMTPServerDisconnected(), False),
        (TimeoutError(), False),
        (ConnectionRefusedError(), False),
    ],
)
def test_is_undeliverable(error: BaseException, will_always_fail: bool) -> None:
    """Tests only an address refused for good or a 5xx rejection is given up on, while an address refused only for now
    (4xx), a setting the user can fix (password or sender) or a mail server that is down or busy is tried again
    later."""
    assert is_undeliverable(error) is will_always_fail


def test_send_retries_a_busy_server_reply() -> None:
    """Tests a 4xx reply (the server is busy) is tried again, and the email is sent on the next try."""
    FakeSMTP.send_failures = [smtplib.SMTPDataError(451, b"Try again later")]

    SmtpEmailSender(smtp_settings()).send(EMAIL)

    assert [len(connection.sent_messages) for connection in FakeSMTP.connections] == [0, 1]


def test_send_waits_longer_after_each_failed_try_up_to_the_longest_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the wait between tries grows from the shortest wait and stops at the longest wait."""
    waits: list[float] = []
    monkeypatch.setattr("tenacity.nap.time.sleep", waits.append)
    retry = RetrySettings(max_attempts=4, min_wait_seconds=1, max_wait_seconds=3, multiplier=1)
    FakeSMTP.send_failures = [TimeoutError()] * 3

    SmtpEmailSender(replace(smtp_settings(), retry=retry)).send(EMAIL)

    assert waits == [1, 2, 3]


class FailingLoginSMTP(FakeSMTP):
    """A mail server that refuses the login."""

    def login(self, user: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"Username and password not accepted")


class FailingCertificateSMTP(FakeSMTP):
    """A mail server on port 587 whose certificate does not match its name, e.g. a server pretending to be it."""

    def starttls(self, context: ssl.SSLContext) -> None:
        raise ssl.SSLCertVerificationError("certificate verify failed: Hostname mismatch")


@pytest.mark.parametrize(
    ("server", "port", "error"),
    [
        (FailingLoginSMTP, 465, smtplib.SMTPAuthenticationError),
        (FailingCertificateSMTP, 587, ssl.SSLCertVerificationError),
    ],
)
def test_a_connection_that_cannot_be_secured_or_logged_in_is_closed_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, server: type[FakeSMTP], port: int, error: type[Exception]
) -> None:
    """Tests a wrong password or a server whose certificate fails the check is reported straight away, without
    retrying, and the half-open connection is closed rather than left open."""
    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP_SSL", server)
    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP", server)

    with pytest.raises(error):
        SmtpEmailSender(smtp_settings(port=port)).send(EMAIL)

    [connection] = FakeSMTP.connections
    assert connection.closed
    assert connection.sent_messages == []


def test_a_with_block_that_sends_nothing_opens_no_connection() -> None:
    """Tests a run with nobody to email does not connect to the mail server at all."""
    with SmtpEmailSender(smtp_settings()):
        pass

    assert FakeSMTP.connections == []


def test_the_connection_is_closed_even_when_the_server_has_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests leaving a `with` block does not fail when the server already dropped the connection, and it is still
    closed."""

    class GoneSMTP(FakeSMTP):
        """A mail server that has dropped the connection, so saying goodbye fails."""

        def quit(self) -> None:
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP_SSL", GoneSMTP)
    sender = SmtpEmailSender(smtp_settings())

    with sender:
        sender.send(EMAIL)

    [connection] = FakeSMTP.connections
    assert connection.closed


def test_a_failed_email_in_a_with_block_does_not_stop_the_next_one() -> None:
    """Tests an email the server rejects drops the shared connection, and the next email reconnects and is sent."""
    FakeSMTP.send_failures = [smtplib.SMTPDataError(554, b"Rejected as spam")]
    sender = SmtpEmailSender(smtp_settings())

    with sender:
        with pytest.raises(smtplib.SMTPDataError):
            sender.send(EMAIL)
        sender.send(EMAIL)

    first, second = FakeSMTP.connections
    assert first.closed and first.sent_messages == []
    assert second.closed and len(second.sent_messages) == 1


def test_get_email_sender_sends_through_the_configured_mail_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the app's sender uses the mail server, login and From address set in the settings (`.env`)."""
    monkeypatch.setattr(config, "smtp_host", "smtp.configured.example")
    monkeypatch.setattr(config, "smtp_port", 587)
    monkeypatch.setattr(config, "email", "inwebstigator@configured.example")
    monkeypatch.setattr(config, "email_password", SecretStr("configured-password"))

    get_email_sender().send(EMAIL)

    [connection] = FakeSMTP.connections
    assert (connection.host, connection.port) == ("smtp.configured.example", 587)
    assert_verifies_certificates(connection.starttls_context)
    assert connection.logged_in == ("inwebstigator@configured.example", "configured-password")
    assert [message["From"] for message in connection.sent_messages] == ["inwebstigator@configured.example"]


def test_emails_sent_at_the_same_time_from_two_threads_do_not_share_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests two emails sent at once from different threads, as the confirmation emails to a new website's recipients
    are, are not written to one connection at the same time, which would mix up their SMTP commands."""
    first_email_sending = threading.Event()
    second_email_sent = threading.Event()

    class SlowSMTP(FakeSMTP):
        """A mail server that takes a while to accept the first email, and notes any email sent over it meanwhile."""

        def __init__(self, host: str, port: int, timeout: float, context: ssl.SSLContext | None = None) -> None:
            super().__init__(host, port, timeout, context)
            self.sending: bool = False
            self.overlapping_sends: int = 0

        def send_message(self, msg: EmailMessage) -> None:
            self.overlapping_sends += self.sending
            self.sending = True
            if msg["To"] == "first@example.com":
                first_email_sending.set()
                second_email_sent.wait(timeout=0.5)
            super().send_message(msg)
            self.sending = False

    monkeypatch.setattr("app.backend.email_service.delivery.smtplib.SMTP_SSL", SlowSMTP)
    sender = SmtpEmailSender(smtp_settings())
    first_email = OutgoingEmail(to="first@example.com", subject="Subject", html_body="<p>Hello</p>")
    second_email = OutgoingEmail(to="second@example.com", subject="Subject", html_body="<p>Hello</p>")
    first_thread = threading.Thread(target=sender.send, args=(first_email,))

    first_thread.start()
    first_email_sending.wait(timeout=2)
    sender.send(second_email)
    second_email_sent.set()
    first_thread.join(timeout=2)

    connections = [connection for connection in FakeSMTP.connections if isinstance(connection, SlowSMTP)]
    assert sorted(message["To"] for connection in connections for message in connection.sent_messages) == [
        "first@example.com",
        "second@example.com",
    ]
    assert [connection.overlapping_sends for connection in connections] == [0] * len(connections)
    assert all(connection.closed for connection in connections)
