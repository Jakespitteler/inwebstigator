import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Return the path to an application resource."""
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return base.joinpath(*parts)
