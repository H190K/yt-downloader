"""core.config: defaults, sanitising, legacy migration and atomic save (all under tmp_path)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import config


@pytest.fixture
def cfg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    data = tmp_path / "data"
    paths = {"config": data / "config.json", "legacy": tmp_path / "home" / ".youtube_downloader_config.json"}
    monkeypatch.setattr(config, "CONFIG_FILE", paths["config"])
    monkeypatch.setattr(config, "LEGACY_CONFIG_FILE", paths["legacy"])
    monkeypatch.setattr(config, "ensure_dirs", lambda: data.mkdir(parents=True, exist_ok=True))
    return paths


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def test_first_run_writes_defaults(cfg_paths):
    cfg = config.load_config()
    assert cfg == config.default_config()
    assert cfg_paths["config"].is_file(), "first run should persist the defaults"
    assert json.loads(cfg_paths["config"].read_text(encoding="utf-8")) == cfg


def test_defaults_values():
    d = config.default_config()
    assert d["theme"] == "dark"
    assert d["default_kind"] == "mp4"
    assert d["default_quality"] == "best"
    assert d["mp3_bitrate"] == "320"
    assert d["cookies_browser"] is None
    assert d["auto_check_updates"] is True
    assert d["last_update_check"] == 0.0
    assert d["download_dir"]


def test_legacy_download_path_migrated(cfg_paths, tmp_path):
    legacy_dir = str(tmp_path / "My Videos")
    _write(cfg_paths["legacy"], {"download_path": legacy_dir, "other": 1})
    cfg = config.load_config()
    assert cfg["download_dir"] == legacy_dir
    # persisted, so the migration happens only once
    assert json.loads(cfg_paths["config"].read_text(encoding="utf-8"))["download_dir"] == legacy_dir


@pytest.mark.parametrize("legacy", [{"download_path": ""}, {"download_path": 5}, {}, "not json", []])
def test_bad_legacy_ignored(cfg_paths, legacy):
    _write(cfg_paths["legacy"], legacy)
    assert config.load_config()["download_dir"] == config.default_config()["download_dir"]


def test_existing_config_wins_over_legacy(cfg_paths, tmp_path):
    _write(cfg_paths["legacy"], {"download_path": str(tmp_path / "legacy")})
    _write(cfg_paths["config"], {"download_dir": str(tmp_path / "current")})
    assert config.load_config()["download_dir"] == str(tmp_path / "current")


def test_corrupt_config_falls_back_to_defaults(cfg_paths):
    _write(cfg_paths["config"], "{ not json")
    cfg = config.load_config()
    assert cfg == config.default_config()


def test_invalid_values_sanitized_and_unknown_keys_kept(cfg_paths):
    _write(cfg_paths["config"], {
        "download_dir": "   ",
        "theme": "neon",
        "default_kind": "flac",
        "default_quality": 720,          # int from an old version -> "720"
        "mp3_bitrate": 999,
        "cookies_browser": "safari",
        "auto_check_updates": 0,
        "last_update_check": "yesterday",
        "window_geometry": "800x600",
    })
    cfg = config.load_config()
    assert cfg["download_dir"] == config.default_config()["download_dir"]
    assert cfg["theme"] == "dark"
    assert cfg["default_kind"] == "mp4"
    assert cfg["default_quality"] == "720"
    assert cfg["mp3_bitrate"] == "320"
    assert cfg["cookies_browser"] is None
    assert cfg["auto_check_updates"] is False
    assert cfg["last_update_check"] == 0.0
    assert cfg["window_geometry"] == "800x600"


def test_invalid_quality_becomes_best(cfg_paths):
    _write(cfg_paths["config"], {"default_quality": "999"})
    assert config.load_config()["default_quality"] == "best"


def test_save_roundtrip_is_atomic(cfg_paths, tmp_path):
    cfg = config.default_config()
    cfg.update(theme="light", default_kind="m4a", default_quality="1080", mp3_bitrate="192",
               cookies_browser="firefox", download_dir=str(tmp_path / "dl"), last_update_check=123.5)
    config.save_config(cfg)
    assert config.load_config() == cfg
    leftovers = [p.name for p in cfg_paths["config"].parent.iterdir() if p.name != "config.json"]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_save_sanitizes(cfg_paths):
    config.save_config({"theme": "neon"})
    on_disk = json.loads(cfg_paths["config"].read_text(encoding="utf-8"))
    assert on_disk["theme"] == "dark"
    assert set(config.default_config()) <= set(on_disk)
