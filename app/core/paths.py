import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Return the path to an application resource."""
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]  # type: ignore
    return base.joinpath(*parts)


def app_folder() -> Path:
    """Returns the folder the app runs from: the folder of the packaged app's .exe, or the project's own folder when
    run from source.

    The app's settings file (`.env`) is kept here, so it is found whichever folder the app was started from (e.g. when
    Windows starts it at sign-in).
    """
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
