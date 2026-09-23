"""Persistent user settings stored as JSON in ``CONFIG_FILE``."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from core.paths import CONFIG_FILE, DEFAULT_DOWNLOAD_DIR, ensure_dirs

LEGACY_CONFIG_FILE = Path.home() / ".youtube_downloader_config.json"

THEMES = ("dark", "light", "system")
KINDS = ("mp4", "mp3", "m4a")
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
MP3_BITRATES = ("320", "256", "192", "128")
COOKIE_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera")


def default_config() -> dict[str, Any]:
    """Return a fresh dict with every setting at its default value."""
    return {
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "theme": "dark",
        "default_kind": "mp4",
        "default_quality": "best",
        "mp3_bitrate": "320",
        "cookies_browser": None,
        "auto_check_updates": True,
        "last_update_check": 0.0,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _sanitize(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge ``cfg`` over defaults, dropping invalid values."""
    out = default_config()
    for key in out:
        if key in cfg:
            out[key] = cfg[key]
    # Keep unknown keys too (UI may store extra things like window geometry).
    for key, value in cfg.items():
        if key not in out:
            out[key] = value
    if not isinstance(out["download_dir"], str) or not out["download_dir"].strip():
        out["download_dir"] = str(DEFAULT_DOWNLOAD_DIR)
    if out["theme"] not in THEMES:
        out["theme"] = "dark"
    if out["default_kind"] not in KINDS:
        out["default_kind"] = "mp4"
    if str(out["default_quality"]) not in QUALITIES:
        out["default_quality"] = "best"
    out["default_quality"] = str(out["default_quality"])
    if str(out["mp3_bitrate"]) not in MP3_BITRATES:
        out["mp3_bitrate"] = "320"
    out["mp3_bitrate"] = str(out["mp3_bitrate"])
    if out["cookies_browser"] not in COOKIE_BROWSERS:
        out["cookies_browser"] = None
    out["auto_check_updates"] = bool(out["auto_check_updates"])
    try:
        out["last_update_check"] = float(out["last_update_check"] or 0)
    except (TypeError, ValueError):
        out["last_update_check"] = 0.0
    return out


def load_config() -> dict[str, Any]:
    """Load settings, migrating the legacy download path on first run. Never raises."""
    data = _read_json(CONFIG_FILE)
    if data is None:
        data = {}
        legacy = _read_json(LEGACY_CONFIG_FILE)
        if legacy and isinstance(legacy.get("download_path"), str) and legacy["download_path"].strip():
            data["download_dir"] = legacy["download_path"]
        cfg = _sanitize(data)
        try:
            save_config(cfg)
        except OSError:
            pass
        return cfg
    return _sanitize(data)


def save_config(cfg: dict[str, Any]) -> None:
    """Atomically write settings to ``CONFIG_FILE``."""
    ensure_dirs()
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config", suffix=".tmp", dir=CONFIG_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_sanitize(dict(cfg)), fh, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
