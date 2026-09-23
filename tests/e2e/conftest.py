"""Fixtures/helpers for the real-network end-to-end tests (run with ``--run-network``)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

TEST_URL = "https://youtu.be/5Y3a06rJjoU"
# Anything left behind by an unfinished/killed download or by yt-dlp's intermediate steps.
LEFTOVER_RE = re.compile(
    r"\.(part|ytdl|temp|tmp)$|\.part-Frag\d+|\.f[\w-]+\.\w+$|\.temp\.\w+$|\.compat-tmp\.mp4$"
    r"|\.(webp|jpe?g|png)$", re.I)

_RESULTS: list["Row"] = []
_RESULTS_LOCK = threading.Lock()


@pytest.fixture(scope="session")
def real_deps():
    from core.deps import DependencyManager

    dm = DependencyManager()
    if not dm.is_ready():
        pytest.skip(f"tools missing in {dm.tools_dir}: {dm.missing()} (run: python main.py --setup)")
    return dm


@pytest.fixture(scope="session")
def media_info(real_deps):
    from core.engine import fetch_info

    info = fetch_info(TEST_URL, real_deps)
    assert info.heights, f"no video heights found for {TEST_URL}"
    return info


# ---------------------------------------------------------------- ffprobe
def ffprobe(path: str | os.PathLike[str], deps) -> dict[str, Any]:
    proc = subprocess.run(
        [str(deps.ffprobe_path), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, f"ffprobe failed on {path}: {proc.stderr[-500:]}"
    return json.loads(proc.stdout or "{}")


def main_video(probe: dict[str, Any]) -> dict[str, Any] | None:
    vids = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")]
    return vids[0] if vids else None


def main_audio(probe: dict[str, Any]) -> dict[str, Any] | None:
    auds = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    return auds[0] if auds else None


def expected_height(requested: str, heights: list[int]) -> int:
    """The resolution the engine should deliver: exact, else highest available below, else lowest."""
    if requested == "best":
        return max(heights)
    target = int(requested)
    below = [h for h in heights if h <= target]
    return max(below) if below else min(heights)


def leftovers(folder: Path) -> list[str]:
    """Temp/partial files anywhere under ``folder`` (and non-empty scratch dirs)."""
    bad: list[str] = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            full = os.path.join(root, f)
            if LEFTOVER_RE.search(f) or ".h190k-tmp" in full:
                bad.append(os.path.relpath(full, folder))
    return sorted(bad)


# ---------------------------------------------------------------- results table
@dataclass
class Row:
    test: str
    kind: str
    requested: str
    file: str = ""
    delivered: str = ""
    vcodec: str = ""
    acodec: str = ""
    bitrate: str = ""
    size_mb: str = ""
    seconds: str = ""
    passed: bool = False
    problems: list[str] = field(default_factory=list)


def record(row: Row) -> None:
    """Register a row for the summary table (kept by reference: later problems still show)."""
    with _RESULTS_LOCK:
        _RESULTS.append(row)


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    if not _RESULTS:
        return
    tr = terminalreporter
    tr.section("H190K network stress results")
    cols = ("test", "kind", "requested", "delivered", "vcodec", "acodec", "bitrate", "size_mb",
            "seconds", "result", "file")
    results = [r.__dict__ for r in _RESULTS]
    rows = []
    for r in results:
        rows.append({**{c: str(r.get(c, "")) for c in cols}, "result": "PASS" if r["passed"] else "FAIL"})
    widths = {c: max(len(c), *(len(x[c]) for x in rows)) for c in cols if c != "file"}
    head = " | ".join(c.ljust(widths[c]) for c in cols if c != "file") + " | file"
    tr.write_line(head)
    tr.write_line("-" * len(head))
    for x in rows:
        tr.write_line(" | ".join(x[c].ljust(widths[c]) for c in cols if c != "file") + " | " + x["file"])
    for r in results:
        for p in r["problems"]:
            tr.write_line(f"  FAIL {r['test']} {r['kind']} {r['requested']}: {p}")
    out = os.environ.get("H190K_RESULTS_JSON")
    if out:
        Path(out).write_text(json.dumps(results, indent=2), encoding="utf-8")
