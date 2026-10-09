import logging
from pathlib import Path

import pytest

from app.core.logging_setup import WEB_SERVER_LOGGER, setup_logging


def test_setup_logging_does_nothing_if_logging_is_already_set_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests logging that is already set up (e.g. by the test runner) is left alone, with no log file started."""
    existing_handler = logging.NullHandler()
    monkeypatch.setattr(logging.getLogger(), "handlers", [existing_handler])
    monkeypatch.setattr(logging.getLogger(WEB_SERVER_LOGGER), "handlers", [])
    log_path: Path = tmp_path / "logs" / "inwebstigator.log"

    setup_logging(log_path, max_bytes=1_000, backup_count=2)

    assert logging.getLogger().handlers == [existing_handler]
    assert logging.getLogger(WEB_SERVER_LOGGER).handlers == []
    assert not log_path.parent.exists()


def test_setup_logging_only_keeps_the_newest_few_log_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests a full log file is replaced by a new one, keeping only `backup_count` older files, so the logs cannot
    fill the disk, and each line says how serious it is and where it came from."""
    root_logger = logging.getLogger()
    monkeypatch.setattr(root_logger, "handlers", [])
    monkeypatch.setattr(root_logger, "level", root_logger.level)
    monkeypatch.setattr(logging.getLogger(WEB_SERVER_LOGGER), "handlers", [])
    log_path: Path = tmp_path / "logs" / "inwebstigator.log"

    setup_logging(log_path, max_bytes=500, backup_count=2)
    for message_number in range(50):
        logging.getLogger("app.test").warning(f"Message {message_number:02} " + "x" * 80)
    for handler in root_logger.handlers:
        handler.close()

    assert sorted(path.name for path in log_path.parent.iterdir()) == [
        "inwebstigator.log",
        "inwebstigator.log.1",
        "inwebstigator.log.2",
    ]
    assert "WARNING [app.test] Message 49 " in log_path.read_text(encoding="utf-8")
