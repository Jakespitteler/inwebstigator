from pathlib import Path
import sys


def resource_path(relative_path: str) -> Path:
    """Return the path to a resource in development or PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / relative_path

    return Path(__file__).resolve().parent.parent.parent / relative_path
