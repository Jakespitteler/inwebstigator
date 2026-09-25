import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Return the path to an application resource."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parents[2]

    return base.joinpath(*parts)
