import smtplib
import ssl
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import pytest
from pydantic import SecretStr

from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.email_service.sender import SmtpEmailSender, is_temporary_failure, send_confirmation
from app.backend.email_service.settings import RetrySettings, SmtpSettings
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
        self.starttls_context = context

    def login(self, user: str, password: str) -> None:
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
    monkeypatch.setattr("app.backend.email_service.sender.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("app.backend.email_service.sender.smtplib.SMTP", FakeSMTP)


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
    assert connection.logged_in is not None
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
