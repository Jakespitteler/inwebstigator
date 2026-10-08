import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT: str = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
WEB_SERVER_LOGGER: str = "uvicorn"


def _log_file_handler(log_path: Path, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    """Builds the handler that writes the logs to a file, starting a new file once it gets too big.

    Only the newest few files are kept, so the logs cannot fill up the disk.

    Args:
        log_path: The log file. Its folder is created if it does not exist.
        max_bytes: How big a log file can get before a new one is started.
        backup_count: How many older log files to keep.

    Returns:
        The handler.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")


def setup_logging(log_path: Path, max_bytes: int, backup_count: int) -> None:
    """Sends the app's logs to the console and to a log file.

    The desktop app has no console on testers' computers, so the log file is the only record of what went wrong.
    The web server's own logs (e.g. an error while the app starts) are written to the file too.
    Does nothing if logging has already been set up (e.g. by the test runner).

    Args:
        log_path: The log file.
        max_bytes: How big a log file can get before a new one is started.
        backup_count: How many older log files to keep.
    """
    if logging.getLogger().handlers:
        return
    file_handler: RotatingFileHandler = _log_file_handler(log_path, max_bytes, backup_count)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[logging.StreamHandler(), file_handler])
    logging.getLogger(WEB_SERVER_LOGGER).addHandler(file_handler)
