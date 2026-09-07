"""The safety gate in front of a real send.

Sits between a half filled .env and an email going to the client by accident.
"""

import pytest

from app.email_sender import build_message


@pytest.fixture
def configured(monkeypatch):
    """A .env that's filled in and good to go."""
    monkeypatch.setattr(build_message, "DRY_RUN", False)
    monkeypatch.setattr(build_message, "SMTP_USER", "scan@gmail.com")
    monkeypatch.setattr(build_message, "SMTP_PASS", "app password")
    monkeypatch.setattr(build_message, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(build_message, "CLIENT_TO", ["me@gmail.com"])


def test_a_fully_configured_setup_is_cleared_to_send(configured):
    assert build_message.configuration_problems() == []


def test_dry_run_blocks_sending(configured, monkeypatch):
    monkeypatch.setattr(build_message, "DRY_RUN", True)
    assert any("DRY_RUN" in p for p in build_message.configuration_problems())


def test_a_missing_password_is_caught(configured, monkeypatch):
    monkeypatch.setattr(build_message, "SMTP_PASS", "")
    assert any("EMAIL_PASSWORD" in p for p in build_message.configuration_problems())


def test_a_missing_account_is_caught(configured, monkeypatch):
    monkeypatch.setattr(build_message, "SMTP_USER", "")
    assert any("EMAIL" in p for p in build_message.configuration_problems())


def test_a_missing_recipient_is_caught(configured, monkeypatch):
    # The one that matters. No CLIENT_TO and it goes nowhere but still
    # looks like it worked.
    monkeypatch.setattr(build_message, "CLIENT_TO", [])
    assert any("CLIENT_TO" in p for p in build_message.configuration_problems())


def test_a_missing_mail_server_is_caught(configured, monkeypatch):
    monkeypatch.setattr(build_message, "SMTP_HOST", "")
    assert any("SMTP_HOST" in p for p in build_message.configuration_problems())


def test_an_unconfigured_setup_reports_every_problem_at_once(monkeypatch):
    # List them all at once, otherwise you fix one and hit the next.
    monkeypatch.setattr(build_message, "DRY_RUN", True)
    monkeypatch.setattr(build_message, "SMTP_USER", "")
    monkeypatch.setattr(build_message, "SMTP_PASS", "")
    monkeypatch.setattr(build_message, "SMTP_HOST", "")
    monkeypatch.setattr(build_message, "CLIENT_TO", [])

    assert len(build_message.configuration_problems()) == 5
