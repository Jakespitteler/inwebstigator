from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import pytest

from app.backend.email_service import (
    build_message,
    configuration_problems,
    send_email,
)


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


def test_configuration_problems_no_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_USER", "test@example.com")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PASS", "secret")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "smtp.example.com")

    problems: list[str] = configuration_problems()
    assert problems == []


def test_configuration_problems_all_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_USER", "")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PASS", "")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "")

    problems: list[str] = configuration_problems()
    assert len(problems) == 3
    assert any("EMAIL is empty" in p for p in problems)
    assert any("EMAIL_PASSWORD is empty" in p for p in problems)
    assert any("SMTP_HOST is empty" in p for p in problems)


def test_configuration_problems_partial_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_USER", "")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PASS", "secret")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "smtp.example.com")

    problems: list[str] = configuration_problems()
    assert len(problems) == 1
    assert "EMAIL is empty" in problems[0]


def test_build_message_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.FROM_ADDR", "sender@example.com")
    fixed_time: datetime = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    msg: EmailMessage = build_message(
        subject="Test Subject",
        recipients=["recipient@example.com"],
        now=fixed_time,
    )

    assert msg["Subject"] == "Test Subject"
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Message-ID"].endswith("@example.com>")
    assert msg.is_multipart() is False


def test_build_message_default_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.FROM_ADDR", "sender@example.com")

    msg: EmailMessage = build_message(
        subject="Default Time",
        recipients=["recipient@example.com"],
    )

    assert msg["Subject"] == "Default Time"
    assert msg["Date"] is not None


def test_build_message_with_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.FROM_ADDR", "sender@example.com")
    msg: EmailMessage = build_message(
        subject="HTML Subject",
        recipients=["one@example.com", "two@example.com"],
        html_body="<p>Hello World</p>",
    )

    assert msg["To"] == "one@example.com, two@example.com"
    assert msg.is_multipart() is True


def test_send_email_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PORT", 465)
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_USER", "user@example.com")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PASS", "pass")

    fake_smtp_instance: FakeSMTP | None = None

    def mock_smtp_ssl_init(*args: Any, **kwargs: Any) -> FakeSMTP:
        nonlocal fake_smtp_instance
        fake_smtp_instance = FakeSMTP(*args, **kwargs)
        return fake_smtp_instance

    monkeypatch.setattr("app.backend.notifier.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_init)

    msg: EmailMessage = EmailMessage()
    result: bool = send_email(msg)

    assert result is True
    assert fake_smtp_instance is not None
    assert fake_smtp_instance.logged_in == ("user@example.com", "pass")
    assert len(fake_smtp_instance.sent_messages) == 1
    assert fake_smtp_instance.sent_messages[0] == msg


def test_send_email_success_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PORT", 465)
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_USER", "")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PASS", "")

    fake_smtp_instance: FakeSMTP | None = None

    def mock_smtp_ssl_init(*args: Any, **kwargs: Any) -> FakeSMTP:
        nonlocal fake_smtp_instance
        fake_smtp_instance = FakeSMTP(*args, **kwargs)
        return fake_smtp_instance

    monkeypatch.setattr("app.backend.notifier.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_init)

    msg: EmailMessage = EmailMessage()
    result: bool = send_email(msg)

    assert result is True
    assert fake_smtp_instance is not None
    assert fake_smtp_instance.logged_in is None
    assert len(fake_smtp_instance.sent_messages) == 1


def test_send_email_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.backend.notifier.email_service.SMTP_PORT", 465)

    def mock_smtp_ssl_raise(*args: Any, **kwargs: Any) -> Any:
        raise Exception("Connection refused")

    monkeypatch.setattr("app.backend.notifier.email_service.smtplib.SMTP_SSL", mock_smtp_ssl_raise)

    msg: EmailMessage = EmailMessage()
    result: bool = send_email(msg)

    assert result is False
