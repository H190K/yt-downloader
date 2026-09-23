"""core.deps: version parsing/comparison, versions.json, checksum parsing and temp cleanup.

No network: every remote call is monkeypatched. Tool "executables" are empty files, so any
attempt to run them fails and exercises the fallback paths.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from core import deps as deps_mod
from core.deps import DependencyManager, _friendly_net_error, _norm_version, _version_tuple


@pytest.fixture
def tools_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    return d


def _install_fake_tools(d: Path) -> None:
    for name in ("yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe"):
        (d / name).write_bytes(b"")


# ---------------------------------------------------------------- version helpers
@pytest.mark.parametrize("raw,out", [
    ("v2.9.7", "2.9.7"), ("V1.0", "1.0"), ("2026.08.19", "2026.08.19"),
    ("  v2.0 ", "2.0"), ("version", "version"), ("", None), (None, None),
])
def test_norm_version(raw, out):
    assert _norm_version(raw) == out


@pytest.mark.parametrize("raw,out", [
    ("2026.08.19", (2026, 8, 19)), ("2.9.7", (2, 9, 7)), ("2026.08.19.232323", (2026, 8, 19, 232323)),
])
def test_version_tuple(raw, out):
    assert _version_tuple(raw) == out


@pytest.mark.parametrize("tool,current,latest,expected", [
    ("yt-dlp", None, "2026.09.01", True),
    ("yt-dlp", "2026.08.19", None, False),
    ("yt-dlp", "2026.08.19", "2026.09.01", True),
    ("yt-dlp", "2026.09.01", "2026.09.01", False),
    ("yt-dlp", "2026.09.01", "2026.08.19", False),        # never "update" to an older build
    ("yt-dlp", "2026.08.19", "2026.08.19.232323", True),  # nightly-style suffix is newer
    ("deno", "2.9.7", "2.10.0", True),                    # numeric, not lexicographic
    ("ffmpeg", "2026-08-01", "2026-09-22", True),          # >= 30 days
    ("ffmpeg", "2026-09-10", "2026-09-22", False),         # daily rebuilds not offered
    ("ffmpeg", "2026-08-23", "2026-09-22", True),          # exactly 30 days
    ("ffmpeg", "weird", "2026-09-22", True),               # unparsable -> compare as strings
])
def test_needs_update(tool, current, latest, expected):
    assert DependencyManager._needs_update(tool, current, latest) is expected


# ---------------------------------------------------------------- versions.json / installed_version
def test_missing_tools(tools_dir):
    dm = DependencyManager(tools_dir)
    assert dm.missing() == ["yt-dlp", "ffmpeg", "deno"]
    assert dm.is_ready() is False
    assert dm.installed_version("yt-dlp") is None


def test_ffmpeg_needs_both_exes(tools_dir):
    (tools_dir / "ffmpeg.exe").write_bytes(b"")
    dm = DependencyManager(tools_dir)
    assert "ffmpeg" in dm.missing()
    (tools_dir / "ffprobe.exe").write_bytes(b"")
    assert "ffmpeg" not in dm.missing()


def test_installed_version_falls_back_to_versions_json(tools_dir):
    _install_fake_tools(tools_dir)
    (tools_dir / "versions.json").write_text(
        json.dumps({"yt-dlp": "2026.08.19", "ffmpeg": "2026-09-22", "deno": "v2.9.7"}), encoding="utf-8")
    dm = DependencyManager(tools_dir)
    assert dm.is_ready()
    # the fake exes can't run, so the stored versions are used
    assert dm.installed_version("yt-dlp") == "2026.08.19"
    assert dm.installed_version("ffmpeg") == "2026-09-22"
    assert dm.installed_version("deno") == "2.9.7"
    st = dm.status()
    assert set(st) == {"yt-dlp", "ffmpeg", "deno"}
    assert all(v["installed"] for v in st.values())


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", ""])
def test_corrupt_versions_json(tools_dir, content):
    _install_fake_tools(tools_dir)
    (tools_dir / "versions.json").write_text(content, encoding="utf-8")
    dm = DependencyManager(tools_dir)
    assert dm._read_versions() == {}
    assert dm.installed_version("ffmpeg") is None


def test_write_version_is_atomic_and_merges(tools_dir):
    dm = DependencyManager(tools_dir)
    dm._write_version("yt-dlp", "1")
    dm._write_version("deno", "2")
    assert json.loads((tools_dir / "versions.json").read_text(encoding="utf-8")) == {"yt-dlp": "1", "deno": "2"}
    assert not list(tools_dir.glob("*.tmp"))


# ---------------------------------------------------------------- checksums (network mocked)
H1, H2 = "a" * 64, "b" * 64


@pytest.mark.parametrize("tool,text,expected", [
    ("yt-dlp", f"{H2}  yt-dlp\n{H1}  yt-dlp.exe\n", H1),
    ("ffmpeg", f"{H2}  ffmpeg-n7.zip\n{H1.upper()}  ffmpeg-master-latest-win64-gpl.zip\n", H1),
    ("deno", f"{H1}\n", H1),                     # single-hash file without a name
    ("deno", f"{H1}\n{H2}\n", None),             # ambiguous -> skip verification
    ("yt-dlp", "not a checksum file", None),
])
def test_expected_sha256(tools_dir, monkeypatch, tool, text, expected):
    monkeypatch.setattr(deps_mod, "_http_text", lambda url, accept=None: text)
    assert DependencyManager(tools_dir)._expected_sha256(tool, "https://x") == expected


def test_expected_sha256_network_failure_is_none(tools_dir, monkeypatch):
    def boom(url, accept=None):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(deps_mod, "_http_text", boom)
    assert DependencyManager(tools_dir)._expected_sha256("yt-dlp", "https://x") is None


def test_sha256(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert DependencyManager._sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_update_all_offline_never_raises(tools_dir, monkeypatch):
    _install_fake_tools(tools_dir)
    dm = DependencyManager(tools_dir)
    monkeypatch.setattr(dm, "_latest_release", lambda tool: (None, None))
    monkeypatch.setattr(dm, "installed_version", lambda tool: "1.0")
    res = dm.update_all()
    assert set(res) == {"yt-dlp", "ffmpeg", "deno"}
    assert all(r.startswith("failed") for r in res.values())


@pytest.mark.parametrize("exc,needle", [
    (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "rate limit"),
    (urllib.error.HTTPError("u", 500, "err", {}, None), "HTTP error 500"),
    (urllib.error.URLError("no route"), "network error"),
    (TimeoutError(), "timed out"),
    (ValueError("x"), "x"),
])
def test_friendly_net_error(exc, needle):
    assert needle in _friendly_net_error(exc)


# ---------------------------------------------------------------- cleanup_temp
def test_cleanup_temp_removes_leftovers_only(tools_dir):
    _install_fake_tools(tools_dir)
    (tools_dir / "versions.json").write_text("{}", encoding="utf-8")
    for d in (".yt-dlp-abc123", ".ffmpeg-xyz", ".deno-1"):
        (tools_dir / d).mkdir()
        (tools_dir / d / "partial.zip").write_bytes(b"x")
    (tools_dir / "yt-dlp.exe.part").write_bytes(b"x")
    (tools_dir / "versions.json.tmp").write_bytes(b"x")
    (tools_dir / ".other-dir").mkdir()

    DependencyManager(tools_dir)  # the constructor runs cleanup_temp()

    remaining = sorted(p.name for p in tools_dir.iterdir())
    assert remaining == sorted([".other-dir", "deno.exe", "ffmpeg.exe", "ffprobe.exe", "versions.json",
                                "yt-dlp.exe"])


def test_cleanup_temp_skips_active_work_dir(tools_dir, monkeypatch):
    active = tools_dir / ".yt-dlp-inprogress"
    active.mkdir()
    monkeypatch.setattr(deps_mod, "_ACTIVE_WORK", {str(active)})
    DependencyManager(tools_dir).cleanup_temp()
    assert active.is_dir()


def test_cleanup_temp_missing_dir_is_noop(tmp_path):
    DependencyManager(tmp_path / "does-not-exist").cleanup_temp()
    assert not (tmp_path / "does-not-exist").exists()
