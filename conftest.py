"""Settings for the whole test run.

pytest loads this file before `tests/conftest.py` and before any test imports the app, so the app is built with
these settings. Automatic scans are turned off, and the app's data folder (its database and log file) is a
temporary folder, so running the tests never touches the real database in AppData.
"""

import atexit
import shutil
import tempfile
from pathlib import Path

from app.core.config import config

TEST_DATA_DIR: Path = Path(tempfile.mkdtemp(prefix="inwebstigator-tests-"))

config.automatic_scans = False
config.allowed_hosts = [*config.allowed_hosts, "testserver", "test"]  # The hosts the test clients send
config.user_data_dir = TEST_DATA_DIR
config.db_path = TEST_DATA_DIR / config.db_name
atexit.register(shutil.rmtree, TEST_DATA_DIR, ignore_errors=True)
