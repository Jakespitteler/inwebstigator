import pytest
from pydantic import SecretStr

from app.backend.email_service.settings import SmtpSettings, guess_imap_host
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
