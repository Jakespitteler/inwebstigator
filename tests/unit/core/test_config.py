from pathlib import Path

import pytest

from app.core.config import Config


def test_settings_can_be_set_by_their_name_in_capitals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests a setting is read from the environment by its name in capitals, so it can be changed for one run."""
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("AUTOMATIC_SCANS", "false")
    monkeypatch.setenv("EMAIL_TIME_ZONE", "Australia/Perth")

    settings = Config()

    assert settings.smtp_port == 587
    assert settings.automatic_scans is False
    assert settings.email_time_zone == "Australia/Perth"


def test_the_email_password_is_hidden_when_the_settings_are_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the email password is kept out of the settings' text (e.g. in a log), while the app can still use it."""
    monkeypatch.setenv("EMAIL_PASSWORD", "abcdefghijklmnop")

    settings = Config()

    assert "abcdefghijklmnop" not in repr(settings)
    assert settings.email_password.get_secret_value() == "abcdefghijklmnop"


def test_a_new_api_token_is_made_each_time_the_app_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests each start of the app gets its own random API token, so a token cannot be guessed or reused."""
    monkeypatch.delenv("API_TOKEN", raising=False)

    assert Config().api_token != Config().api_token


def test_the_log_file_is_in_the_apps_data_folder(tmp_path: Path) -> None:
    """Tests the log file is kept in a "logs" folder inside the app's data folder, where INSTALL.md says it is."""
    assert Config(user_data_dir=tmp_path).log_path == tmp_path / "logs" / "inwebstigator.log"


def test_reading_the_settings_does_not_create_the_data_folder(tmp_path: Path) -> None:
    """Tests the database URL points at the database file, and reading the settings leaves the disk alone, as the
    app creates its data folder when it starts."""
    data_dir: Path = tmp_path / "data"
    db_path: Path = data_dir / "inwebstigator.db"

    db_url: str = Config(user_data_dir=data_dir, db_path=db_path).db_url

    assert db_url == f"sqlite:///{db_path.as_posix()}"
    assert not data_dir.exists()
