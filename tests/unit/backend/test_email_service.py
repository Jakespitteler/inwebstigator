import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import pytest

from app.backend.email_service import build_message, confirm_address_can_receive_email, send_email
from app.core.errors import UndeliverableEmailError


class FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int = 30) -> None:
        self.host: str = host
        self.port: int = port
        self.timeout: int = timeout
        self.logged_in: tuple[str, str] | None = None
        self.sent_messages: list[EmailMessage] = []

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        self.sent_messages.append(msg)


def test_build_message_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.FROM_ADDR", "sender@example.com")
    fixed_time: datetime = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    msg: EmailMessage = build_message(
        subject="Test Subject",
        recipients=["recipient@example.com"],
        html_body="",
        now=fixed_time,
    )

    assert msg["Subject"] == "Test Subject"
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Message-ID"].endswith("@example.com>")


def test_build_message_default_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.FROM_ADDR", "sender@example.com")

    msg: EmailMessage = build_message(
        subject="Default Time",
        recipients=["recipient@example.com"],
        html_body="",
    )

    assert msg["Subject"] == "Default Time"
    assert msg["Date"] is not None


def test_build_message_with_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.FROM_ADDR", "sender@example.com")
    msg: EmailMessage = build_message(
        subject="HTML Subject",
        recipients=["one@example.com", "two@example.com"],
        html_body="<p>Hello World</p>",
    )

    assert msg["To"] == "one@example.com, two@example.com"
    assert msg.is_multipart() is True


def test_send_email_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.email_service.SMTP_PORT", 465)
    monkeypatch.setattr("app.backend.email_service.SMTP_USER", "user@example.com")
    monkeypatch.setattr("app.backend.email_service.SMTP_PASS", "pass")

    fake_smtp_instance: FakeSMTP | None = None

    def mock_smtp_ssl_init(*args: Any, **kwargs: Any) -> FakeSMTP:
        nonlocal fake_smtp_instance
        fake_smtp_instance = FakeSMTP(*args, **kwargs)
        return fake_smtp_instance

    monkeypatch.setattr("app.backend.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_init)

    msg: EmailMessage = EmailMessage()
    send_email(msg)  # Returns None on success

    assert fake_smtp_instance is not None
    assert fake_smtp_instance.logged_in == ("user@example.com", "pass")
    assert len(fake_smtp_instance.sent_messages) == 1
    assert fake_smtp_instance.sent_messages[0] == msg


def test_send_email_success_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.email_service.SMTP_PORT", 465)
    monkeypatch.setattr("app.backend.email_service.SMTP_USER", "")
    monkeypatch.setattr("app.backend.email_service.SMTP_PASS", "")

    fake_smtp_instance: FakeSMTP | None = None

    def mock_smtp_ssl_init(*args: Any, **kwargs: Any) -> FakeSMTP:
        nonlocal fake_smtp_instance
        fake_smtp_instance = FakeSMTP(*args, **kwargs)
        return fake_smtp_instance

    monkeypatch.setattr("app.backend.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_init)

    msg: EmailMessage = EmailMessage()
    send_email(msg)

    assert fake_smtp_instance is not None
    assert fake_smtp_instance.logged_in is None
    assert len(fake_smtp_instance.sent_messages) == 1


def test_send_email_failure_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.email_service.SMTP_PORT", 465)

    # Speed up tests by minimizing wait times and setting attempts count
    monkeypatch.setattr("app.backend.email_service.config.email_retry_max_attempts", 3)
    monkeypatch.setattr("app.backend.email_service.config.email_retry_min_wait_seconds", 0)
    monkeypatch.setattr("app.backend.email_service.config.email_retry_max_wait_seconds", 0)
    monkeypatch.setattr("app.backend.email_service.config.email_retry_multiplier", 1)

    attempts = 0

    def mock_smtp_ssl_raise(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("Connection refused")

    monkeypatch.setattr("app.backend.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_raise)

    msg: EmailMessage = EmailMessage()

    # Verify that the exception is re-raised after exhausting retry attempts
    with pytest.raises(TimeoutError, match="Connection refused"):
        send_email(msg)

    # Ensure it tried the configured maximum number of attempts
    assert attempts == 3


def test_send_email_does_not_retry_a_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.email_service.SMTP_HOST", "smtp.example.com")
    attempts = 0

    def mock_smtp_ssl_raise(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise smtplib.SMTPAuthenticationError(535, b"Bad credentials")

    monkeypatch.setattr("app.backend.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_raise)

    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_email(EmailMessage())

    assert attempts == 1


# ==========================
#  Confirming an address
# ==========================


class FakeInbox:
    """Stands in for the inbox connection, so tests do not log in to a real mail account."""

    def __enter__(self) -> "FakeInbox":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


@pytest.fixture()
def sent_confirmations(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Replaces sending with a list of the emails that would have been sent, and skips the waiting."""
    sent: list[EmailMessage] = []
    monkeypatch.setattr("app.backend.email_service.send_email", sent.append)
    monkeypatch.setattr("app.backend.email_service.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.backend.email_service.EMAIL_BOUNCE_WAIT_SECONDS", 6)
    monkeypatch.setattr("app.backend.email_service.EMAIL_BOUNCE_POLL_SECONDS", 3)
    return sent


def _use_inbox_bounce_counts(monkeypatch: pytest.MonkeyPatch, bounce_counts: list[int]) -> None:
    """Makes the inbox report each of the given bounce counts in turn, starting before the email is sent."""
    counts: list[int] = list(bounce_counts)
    monkeypatch.setattr("app.backend.email_service._open_inbox", lambda: FakeInbox())
    monkeypatch.setattr("app.backend.email_service._count_bounces", lambda inbox, address: counts.pop(0))


def test_confirm_address_passes_when_nothing_bounces(
    monkeypatch: pytest.MonkeyPatch, sent_confirmations: list[EmailMessage]
) -> None:
    _use_inbox_bounce_counts(monkeypatch, [0, 0, 0])

    confirm_address_can_receive_email("real@example.com", "Subject", "<p>Hi</p>")

    assert [message["To"] for message in sent_confirmations] == ["real@example.com"]


def test_confirm_address_raises_when_the_email_bounces(
    monkeypatch: pytest.MonkeyPatch, sent_confirmations: list[EmailMessage]
) -> None:
    _use_inbox_bounce_counts(monkeypatch, [1, 1, 2])

    with pytest.raises(UndeliverableEmailError) as error:
        confirm_address_can_receive_email("fake@example.com", "Subject", "<p>Hi</p>")

    assert error.value.address == "fake@example.com"
    assert len(sent_confirmations) == 1


def test_confirm_address_ignores_bounces_from_before_the_email_was_sent(
    monkeypatch: pytest.MonkeyPatch, sent_confirmations: list[EmailMessage]
) -> None:
    _use_inbox_bounce_counts(monkeypatch, [3, 3, 3])

    confirm_address_can_receive_email("real@example.com", "Subject", "<p>Hi</p>")

    assert len(sent_confirmations) == 1


def test_confirm_address_raises_when_the_server_refuses_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(msg: EmailMessage) -> None:
        raise smtplib.SMTPRecipientsRefused({"fake@example.com": (550, b"No such user")})

    monkeypatch.setattr("app.backend.email_service.send_email", refuse)
    _use_inbox_bounce_counts(monkeypatch, [0])

    with pytest.raises(UndeliverableEmailError):
        confirm_address_can_receive_email("fake@example.com", "Subject", "<p>Hi</p>")


def test_confirm_address_still_sends_when_the_inbox_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch, sent_confirmations: list[EmailMessage]
) -> None:
    monkeypatch.setattr("app.backend.email_service._open_inbox", lambda: None)

    confirm_address_can_receive_email("real@example.com", "Subject", "<p>Hi</p>")

    assert len(sent_confirmations) == 1
