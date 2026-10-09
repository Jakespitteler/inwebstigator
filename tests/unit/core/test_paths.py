import sys
from pathlib import Path

import pytest

from app.core.paths import resource_path


def test_resource_path_finds_the_apps_files_when_run_from_source() -> None:
    """Tests a resource is found in the project folder when the app is run from its source code."""
    assert resource_path("app", "frontend", "templates", "index.html").is_file()
    assert resource_path("app", "backend", "email_service", "templates", "email.css").is_file()


def test_resource_path_finds_the_apps_files_in_the_bundle_when_packaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests a resource is looked for in the folder the packaged app unpacks itself to, rather than the source."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_path("app", "frontend", "static", "favicon.ico") == tmp_path / "app" / "frontend" / "static" / (
        "favicon.ico"
    )
