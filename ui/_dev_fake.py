"""Simulated backend with the same interface as ``core`` (see CONTRACT.md).

Enabled with ``H190K_FAKE_BACKEND=1``. Extra knobs for exercising UI states:

* ``H190K_FAKE_READY=1``      - tools are already installed (skip the setup screen)
* ``H190K_FAKE_FAIL_SETUP=1`` - the first install attempt fails (to test Retry)
* URL containing ``fail``      - fetch raises an EngineError
* URL containing ``insta``     - fetch raises the Instagram login EngineError
* URL containing ``list``      - playlist with 12 entries
* URL containing ``shorts``    - vertical video with non-standard heights
* URL containing ``soundcloud``/``audio`` - audio-only media
* URL containing ``err``       - the download fails half way
* URL containing ``hd1080``    - source only has up to 1080p: asking for 4K saves 1080p
  (``delivered_quality`` differs from the request; shown as a success with a note)
* URL containing ``warn``      - success with a warning message ("Finished with errors: ...")
* URL containing ``late``      - after on_done(ok) a stray progress update and a second,
  failing on_done arrive (the finished row must stay "Completed")
* URL containing ``why``       - like ``hd1080`` but the engine explains the lower quality in
  ``job.warning`` (the row shows that text, in the warning color)
* URL containing ``403``       - fails with ``error_kind="http_403"``, a multi-line friendly
  message and ``suggested_quality="1080"``; the retry at <= 1080p succeeds ("Try 1080p")
* URL containing ``login``     - fails with ``error_kind="login_required"`` ("Open Settings")
* URL containing ``long``      - fails with a very long message (collapsed, "Show more")

Finished jobs mimic the real engine: ``output_path`` is ``<title> [<id>] - 720p.mp4`` /
``- 320kbps.mp3`` / ``- 128kbps.m4a`` and ``delivered_quality`` is set ("720p", "320kbps").
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.paths import resource_path  # noqa: F401  (stdlib-only module, safe to import)

TOOLS = ("yt-dlp", "ffmpeg", "deno")
TOOLS_DIR = Path(tempfile.gettempdir()) / "h190k_fake_tools"
_CONFIG = Path(tempfile.gettempdir()) / "h190k_fake_config.json"
_STATE = {"ready": os.environ.get("H190K_FAKE_READY") == "1",
          "fail_next": os.environ.get("H190K_FAKE_FAIL_SETUP") == "1",
          "yt-dlp": "2025.08.20"}

Progress = Callable[[str, str, "float | None"], None]


def ensure_dirs() -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------- config
_DEFAULTS: dict[str, Any] = {
    "download_dir": str(Path.home() / "Downloads" / "H190K Downloader"),
    "theme": "dark", "default_kind": "mp4", "default_quality": "best", "mp3_bitrate": "320",
    "cookies_browser": None, "auto_check_updates": True, "last_update_check": 0.0,
}


def load_config() -> dict[str, Any]:
    cfg = dict(_DEFAULTS)
    try:
        cfg.update(json.loads(_CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if os.environ.get("H190K_FAKE_THEME"):
        cfg["theme"] = os.environ["H190K_FAKE_THEME"]
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    _CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- deps
class DependencyError(Exception):
    pass


class DependencyManager:
    def __init__(self, tools_dir: Path = TOOLS_DIR) -> None:
        self.tools_dir = tools_dir
        self.ytdlp_path = tools_dir / "yt-dlp.exe"
        self.ffmpeg_dir = tools_dir
        self.deno_path = tools_dir / "deno.exe"

    def status(self) -> dict[str, dict]:
        time.sleep(0.3)
        ready = _STATE["ready"]
        versions = {"yt-dlp": _STATE["yt-dlp"], "ffmpeg": "2025-09-20", "deno": "2.4.5"}
        return {t: {"installed": ready, "version": versions[t] if ready else None,
                    "path": str(self.tools_dir / f"{t}.exe")} for t in TOOLS}

    def missing(self) -> list[str]:
        return [] if _STATE["ready"] else list(TOOLS)

    def is_ready(self) -> bool:
        return bool(_STATE["ready"])

    def install_missing(self, progress: Progress | None = None) -> None:
        progress = progress or (lambda *a: None)
        for tool, mb in (("yt-dlp", 18), ("ffmpeg", 95), ("deno", 42)):
            progress(tool, "Connecting...", None)
            time.sleep(0.6)
            for i in range(1, 21):
                time.sleep(0.05)
                progress(tool, f"Downloading {mb * i / 20:.1f} / {mb} MB", i / 20)
                if tool == "ffmpeg" and i == 12 and _STATE["fail_next"]:
                    _STATE["fail_next"] = False
                    raise DependencyError("Could not download FFmpeg: the connection timed out. "
                                          "Check your internet connection and try again.")
            if tool == "ffmpeg":
                progress(tool, "Extracting...", None)
                time.sleep(0.8)
            progress(tool, "Installed", 1.0)
        _STATE["ready"] = True

    def check_updates(self) -> dict[str, dict]:
        time.sleep(1.2)
        return {
            "yt-dlp": {"current": _STATE["yt-dlp"], "latest": "2025.09.05",
                       "update_available": _STATE["yt-dlp"] != "2025.09.05"},
            "ffmpeg": {"current": "2025-09-20", "latest": "2025-09-20", "update_available": False},
            "deno": {"current": "2.4.5", "latest": "2.4.5", "update_available": False},
        }

    def update_all(self, progress: Progress | None = None) -> dict[str, str]:
        progress = progress or (lambda *a: None)
        out: dict[str, str] = {}
        for tool in TOOLS:
            progress(tool, "Checking...", None)
            time.sleep(0.4)
            if tool == "yt-dlp" and _STATE["yt-dlp"] != "2025.09.05":
                for i in range(1, 11):
                    time.sleep(0.08)
                    progress(tool, "Downloading update", i / 10)
                _STATE["yt-dlp"] = "2025.09.05"
                out[tool] = "updated to 2025.09.05"
            else:
                out[tool] = "up to date"
        return out


# ----------------------------------------------------------------------------- engine
class EngineError(Exception):
    pass


@dataclass
class MediaInfo:
    url: str
    title: str
    uploader: str | None
    duration: float | None
    thumbnail: str | None
    extractor: str
    webpage_url: str
    is_playlist: bool
    entry_count: int
    heights: list[int] = field(default_factory=list)
    has_video: bool = True


_FULL_LADDER = [2160, 1440, 1080, 720, 480, 360, 240]


def _fake_heights(url: str) -> list[int]:
    """Video heights the simulated source offers (shared by fetch_info and DownloadJob)."""
    low = url.lower()
    if "soundcloud" in low or "audio" in low:
        return []
    if "shorts" in low:
        return [1920, 1280, 854, 640]
    if "hd1080" in low or "list" in low:
        return [1080, 720, 480, 360]
    return list(_FULL_LADDER)


def _fake_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]


def fetch_info(url: str, deps: DependencyManager, cookies_browser: str | None = None,
               cancel_event: threading.Event | None = None) -> MediaInfo:
    for _ in range(12):
        time.sleep(0.1)
        if cancel_event is not None and cancel_event.is_set():
            raise EngineError("Cancelled")
    low = url.lower()
    if "insta" in low and not cookies_browser:
        raise EngineError("Instagram requires login - choose a browser for cookies in Settings.")
    if "fail" in low:
        raise EngineError("This video is unavailable. It may be private or removed.")
    if "soundcloud" in low or "audio" in low:
        return MediaInfo(url, "Nightcall (Extended Mix) - Late Night Drive Sessions", "Synthwave Radio",
                         412.0, "https://picsum.photos/seed/h190k2/640/640", "soundcloud", url,
                         False, 1, [], False)
    if "shorts" in low:
        return MediaInfo(url, "Crazy skateboard trick #shorts", "SkateDaily", 31.0,
                         "https://picsum.photos/seed/h190k4/360/640", "youtube", url, False, 1,
                         _fake_heights(url), True)
    if "list" in low:
        return MediaInfo(url, "Python for Beginners - Complete Course Playlist", "Code Academy",
                         None, "https://picsum.photos/seed/h190k3/640/360", "youtube:tab", url,
                         True, 12, _fake_heights(url), True)
    return MediaInfo(url, "Exploring the Swiss Alps in 4K - A Cinematic Journey Through Mountains, "
                          "Lakes and Tiny Villages", "Wanderlust Films", 1234.0,
                     "https://picsum.photos/seed/h190k/640/360", "youtube", url, False, 1,
                     _fake_heights(url), True)


@dataclass
class JobOptions:
    kind: str
    quality: str
    mp3_bitrate: str = "320"
    out_dir: str = ""
    playlist: bool = False
    cookies_browser: str | None = None
    embed_thumbnail: bool = True
    embed_metadata: bool = True


class DownloadJob:
    def __init__(self, url: str, title: str, options: JobOptions, deps: DependencyManager) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.title = title
        self.options = options
        self.deps = deps
        self.state = "queued"
        self.output_path: str | None = None
        self.delivered_quality: str | None = None
        self.warning: str | None = None
        self.already_downloaded = False
        self.error: str | None = None
        self.error_kind: str | None = None
        self.suggested_quality: str | None = None
        self._cancel = threading.Event()

    def start(self, on_progress: Callable[["DownloadJob", dict], None],
              on_done: Callable[["DownloadJob", bool, str], None]) -> None:
        threading.Thread(target=self._run, args=(on_progress, on_done), daemon=True).start()

    def _run(self, on_progress: Callable, on_done: Callable) -> None:
        self.state = "downloading"
        items = 12 if self.options.playlist else 1
        steps = 40 if items == 1 else 8
        for item in range(1, items + 1):
            for i in range(steps + 1):
                if self._cancel.wait(0.08 + random.random() * 0.05):
                    self.state = "cancelled"
                    on_done(self, False, "Cancelled")
                    return
                failure = self._failure() if i == steps // 2 else None
                if failure is not None:
                    self.state = "error"
                    self.error = failure
                    on_done(self, False, failure)
                    return
                frac = i / steps
                on_progress(self, {
                    "fraction": frac, "percent": f"{frac * 100:.1f}%",
                    "speed": f"{random.uniform(2, 9):.2f}MiB/s", "eta": f"00:{max(0, steps - i):02d}",
                    "status": "downloading", "message": "", "item": f"{item}/{items}" if items > 1 else "",
                })
        self.state = "processing"
        on_progress(self, {"fraction": None, "percent": "", "speed": "", "eta": "",
                           "status": "processing", "message": "Merging video and audio", "item": ""})
        time.sleep(1.2)
        self.delivered_quality = self._delivered()
        self.output_path = self._output_name()
        wanted = self.options.quality
        if "why" in self.url.lower() and wanted.isdigit() and int(wanted) > 1080:
            self.warning = (f"This video is only available up to 1080p, so 1080p was saved "
                            f"instead of {self.options.quality}p.")
        self.state = "done"
        low = self.url.lower()
        if "warn" in low:
            on_done(self, True, "Finished with errors: 1 of 12 item(s) failed. "
                                f"Saved to {self.output_path}")
            return
        on_done(self, True, self.output_path)
        if "late" in low:  # misbehaving backend: stray callbacks after completion
            time.sleep(0.3)
            on_progress(self, {"fraction": 0.5, "percent": "50.0%", "speed": "1.00MiB/s",
                               "eta": "00:05", "status": "downloading", "message": "", "item": ""})
            on_done(self, False, "Spurious failure after success")

    def _failure(self) -> str | None:
        """Simulated failure for this URL/options, or None (sets error_kind/suggested_quality)."""
        low = self.url.lower()
        if "403" in low:
            q = self.options.quality
            if self.options.kind == "mp4" and (not q.isdigit() or int(q) > 1080):
                self.error_kind = "http_403"
                self.suggested_quality = "1080"
                return ("YouTube refused the download of the high-quality stream (HTTP 403 "
                        "Forbidden).\nThis happens with some videos when the 4K/1440p formats "
                        "need extra verification.\nLower qualities of this video still work - "
                        "try 1080p, or update the tools from About & updates.")
            return None
        if "login" in low:
            self.error_kind = "login_required"
            return ("This video needs you to be signed in.\nChoose the browser you're logged "
                    "in with under Settings > Cookies, then try again.")
        if "long" in low:
            self.error_kind = "unknown"
            details = "\n".join(f"Detail line {n}: the server closed the connection while "
                                f"sending fragment {n * 7} of 212." for n in range(1, 7))
            return "The download failed after several attempts. " * 4 + "\n" + details
        if "err" in low:
            self.error_kind = "http_403"
            return ("HTTP Error 403: Forbidden. The site blocked the download - "
                    "try again later or update yt-dlp.")
        return None

    def _delivered(self) -> str:
        """What the simulated source can actually provide for the requested options."""
        kind = self.options.kind
        if kind == "mp3":
            return f"{self.options.mp3_bitrate}kbps"
        if kind == "m4a":
            return "128kbps"
        heights = sorted(_fake_heights(self.url), reverse=True) or [720]
        if "why" in self.url.lower():
            heights = [1080, 720, 480, 360]
        if self.options.quality.isdigit():
            want = int(self.options.quality)
            fit = [h for h in heights if h <= want]
            return f"{fit[0] if fit else heights[-1]}p"
        return f"{heights[0]}p"

    def _output_name(self) -> str:
        safe = re.sub(r'[<>:"/\\|?*]', "_", self.title)[:60].strip() or "video"
        if self.options.playlist:
            return str(Path(self.options.out_dir) / safe)
        name = f"{safe} [{_fake_id(self.url)}] - {self.delivered_quality}.{self.options.kind}"
        return str(Path(self.options.out_dir) / name)

    def cancel(self) -> None:
        self._cancel.set()
        if self.state == "queued":
            self.state = "cancelled"
