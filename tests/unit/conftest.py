"""Fixtures for the fast, offline unit tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_deps(tmp_path: Path) -> SimpleNamespace:
    """A stand-in for DependencyManager with the attributes the engine reads.

    The tool files exist (empty) so ``deno_path.is_file()`` / ``_require_ready`` behave as when
    the tools are installed. Nothing is ever executed.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe"):
        (bin_dir / name).write_bytes(b"")
    return SimpleNamespace(
        tools_dir=bin_dir,
        ytdlp_path=bin_dir / "yt-dlp.exe",
        ffmpeg_dir=bin_dir,
        ffmpeg_path=bin_dir / "ffmpeg.exe",
        ffprobe_path=bin_dir / "ffprobe.exe",
        deno_path=bin_dir / "deno.exe",
        versions_file=bin_dir / "versions.json",
    )

