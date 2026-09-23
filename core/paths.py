"""Filesystem locations used by the application.

Importing this module has no side effects on disk; call :func:`ensure_dirs` to create folders.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "H190K Downloader"

FROZEN: bool = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR: Path = Path(sys.executable).resolve().parent
    RESOURCE_DIR: Path = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = APP_DIR


def resource_path(rel: str) -> Path:
    """Return the absolute path of a bundled resource (works frozen and in dev)."""
    return RESOURCE_DIR / rel


def _is_writable(directory: Path) -> bool:
    """Return True if files can be created inside ``directory``."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".wtest", dir=directory)
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def _pick_data_dir() -> Path:
    portable = APP_DIR / "data"
    if portable.is_dir() and _is_writable(portable):
        return portable
    if _is_writable(APP_DIR):
        return portable
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


DATA_DIR: Path = _pick_data_dir()
TOOLS_DIR: Path = DATA_DIR / "bin"
CONFIG_FILE: Path = DATA_DIR / "config.json"
DEFAULT_DOWNLOAD_DIR: Path = Path.home() / "Downloads" / APP_NAME


def ensure_dirs() -> None:
    """Create the data and tools directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
