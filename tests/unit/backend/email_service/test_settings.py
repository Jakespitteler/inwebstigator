import pytest
from pydantic import SecretStr

from app.backend.email_service.settings import InboxSettings, RetrySettings, SmtpSettings, guess_imap_host
from app.core.config import config


@pytest.mark.parametrize(
    ("smtp_host", "imap_host"),
    [
        ("smtp.gmail.com", "imap.gmail.com"),
        ("smtp.office365.com", "outlook.office365.com"),
        ("smtp-mail.outlook.com", "outlook.office365.com"),
        ("mail.example.com", None),
    ],
)
def test_guess_imap_host(smtp_host: str, imap_host: str | None) -> None:
    assert guess_imap_host(smtp_host) == imap_host


def test_smtp_settings_keep_the_password_secret_until_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the password is not held as plain text, so it cannot show up in logs or error reports."""
    monkeypatch.setattr(config, "email_password", SecretStr("app-password"))

    settings = SmtpSettings.from_config()

    assert "app-password" not in repr(settings)
    assert settings.password.get_secret_value() == "app-password"


@pytest.mark.parametrize(("port", "uses_implicit_tls"), [(465, True), (587, False)])
def test_smtp_settings_choose_how_to_encrypt_from_the_port(port: int, uses_implicit_tls: bool) -> None:
    settings = SmtpSettings(
        host="smtp.example.com",
        port=port,
        timeout_seconds=30,
        username="",
        password=SecretStr(""),
        sender_address="sender@example.com",
    )

    assert settings.uses_implicit_tls is uses_implicit_tls


def use_mail_settings(monkeypatch: pytest.MonkeyPatch, smtp_host: str, imap_host: str) -> None:
    """Sets the mail settings as if they were read from `.env`."""
    monkeypatch.setattr(config, "email", "sender@example.com")
    monkeypatch.setattr(config, "email_password", SecretStr("app-password"))
    monkeypatch.setattr(config, "smtp_host", smtp_host)
    monkeypatch.setattr(config, "smtp_port", 587)
    monkeypatch.setattr(config, "smtp_timeout_seconds", 20)
    monkeypatch.setattr(config, "imap_host", imap_host)
    monkeypatch.setattr(config, "imap_port", 1993)
    monkeypatch.setattr(config, "email_bounce_wait_seconds", 40)
    monkeypatch.setattr(config, "email_bounce_poll_seconds", 5)
    monkeypatch.setattr(config, "email_retry_max_attempts", 4)


def test_smtp_settings_are_read_from_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the mail server, login, From address and retries come from the settings, sending as the login account."""
    use_mail_settings(monkeypatch, smtp_host="smtp.gmail.com", imap_host="")

    settings = SmtpSettings.from_config()

    assert (settings.host, settings.port, settings.timeout_seconds) == ("smtp.gmail.com", 587, 20)
    assert (settings.username, settings.sender_address) == ("sender@example.com", "sender@example.com")
    assert settings.retry == RetrySettings.from_config()
    assert settings.retry.max_attempts == 4


def test_inbox_settings_are_read_from_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the inbox is logged in to as the sending account, with the configured port, timeout and bounce wait."""
    use_mail_settings(monkeypatch, smtp_host="smtp.gmail.com", imap_host="imap.custom.example")

    settings = InboxSettings.from_config()

    assert (settings.host, settings.port, settings.timeout_seconds) == ("imap.custom.example", 1993, 20)
    assert (settings.username, settings.password.get_secret_value()) == ("sender@example.com", "app-password")
    assert (settings.bounce_wait_seconds, settings.bounce_poll_seconds) == (40, 5)


@pytest.mark.parametrize(
    ("smtp_host", "imap_host", "expected_inbox_host"),
    [
        ("smtp.gmail.com", "", "imap.gmail.com"),
        ("smtp.office365.com", "", "outlook.office365.com"),
        ("mail.example.com", "", None),
        ("smtp.gmail.com", "imap.custom.example", "imap.custom.example"),
    ],
)
def test_inbox_server_is_worked_out_from_the_smtp_server_unless_it_is_set(
    monkeypatch: pytest.MonkeyPatch, smtp_host: str, imap_host: str, expected_inbox_host: str | None
) -> None:
    """Tests a blank IMAP_HOST is worked out from SMTP_HOST, and a set IMAP_HOST is used as it is."""
    use_mail_settings(monkeypatch, smtp_host=smtp_host, imap_host=imap_host)

    assert InboxSettings.from_config().host == expected_inbox_host
