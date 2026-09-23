"""Download engine: drives the standalone ``yt-dlp.exe`` as a subprocess.

Public API (see CONTRACT.md): :class:`MediaInfo`, :class:`EngineError`, :func:`fetch_info`,
:class:`JobOptions`, :class:`DownloadJob`.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.deps import CREATE_NO_WINDOW, DependencyManager
from core.paths import DATA_DIR

KINDS = ("mp4", "mp3", "m4a")
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
MP3_BITRATES = ("320", "256", "192", "128")
COOKIE_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera")

# Audio codecs that play everywhere inside an .mp4 (Windows Media Player / Films & TV).
_MP4_OK_AUDIO = {"aac", "mp3", "alac"}
_AAC_BITRATE = "192k"
# Video codecs Windows can't play out of the box (HEVC needs a paid Store extension).
_MP4_REENCODE_VIDEO = {"hevc"}

_SEP = "\x1f"  # unit separator: never appears in yt-dlp's numeric/progress fields
_DL_TAG = "[H190K-DL]"
_PP_TAG = "[H190K-PP]"
_FILE_TAG = "[H190K-FILE]"

_PP_LABELS: dict[str, str] = {
    "Merger": "Merging video and audio...",
    "ExtractAudio": "Converting audio...",
    "EmbedThumbnail": "Embedding thumbnail...",
    "ThumbnailsConvertor": "Preparing thumbnail...",
    "Metadata": "Writing metadata...",
    "FFmpegMetadata": "Writing metadata...",
    "VideoConvertor": "Converting video...",
    "VideoRemuxer": "Remuxing video...",
    "FixupM3u8": "Fixing container...",
    "FixupM4a": "Fixing container...",
    "FixupTimestamp": "Fixing timestamps...",
    "FixupDuration": "Fixing duration...",
    "FixupStretched": "Fixing aspect ratio...",
    "MoveFiles": "Finishing...",
}
_PP_LINE_RE = re.compile(r"^\[(%s)\]" % "|".join(re.escape(k) for k in _PP_LABELS))


# ====================================================================== data types
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


class EngineError(Exception):
    """Error whose message is suitable for showing to the user."""


@dataclass
class JobOptions:
    kind: str = "mp4"
    quality: str = "best"
    mp3_bitrate: str = "320"
    out_dir: str = ""
    playlist: bool = False
    cookies_browser: str | None = None
    embed_thumbnail: bool = True
    embed_metadata: bool = True


ProgressCb = Callable[["DownloadJob", dict], None]
DoneCb = Callable[["DownloadJob", bool, str], None]


# ====================================================================== helpers
def _site_name(extractor: str | None, url: str) -> str:
    ex = (extractor or "").split(":")[0].lower()
    known = {
        "youtube": "YouTube", "youtubetab": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
        "facebook": "Facebook", "twitter": "X/Twitter", "soundcloud": "SoundCloud", "vimeo": "Vimeo",
        "reddit": "Reddit", "twitch": "Twitch", "dailymotion": "Dailymotion",
    }
    if ex in known:
        return known[ex]
    host = (urlparse(url).hostname or "").lower()
    for key, name in (("youtu", "YouTube"), ("instagram", "Instagram"), ("tiktok", "TikTok"),
                      ("facebook", "Facebook"), ("fb.watch", "Facebook"), ("twitter", "X/Twitter"),
                      ("x.com", "X/Twitter"), ("soundcloud", "SoundCloud"), ("vimeo", "Vimeo"),
                      ("reddit", "Reddit"), ("redd.it", "Reddit")):
        if key in host:
            return name
    return host.removeprefix("www.") or "This site"


def friendly_error(lines: list[str] | str, url: str = "", cookies_browser: str | None = None) -> str:
    """Translate yt-dlp stderr output into a short, actionable message."""
    text = lines if isinstance(lines, str) else "\n".join(lines)
    errors = [ln for ln in text.splitlines() if ln.startswith("ERROR:")]
    main = errors[-1] if errors else (text.strip().splitlines()[-1] if text.strip() else "")
    low = text.lower()
    site = _site_name(None, url)
    cookie_hint = (
        "Choose a browser for cookies in Settings (and make sure you're logged in there)."
        if not cookies_browser
        else f"Make sure you're logged in to {site} in {cookies_browser.title()}, then try again."
    )

    browser = (cookies_browser or "the browser").title()
    if "failed to decrypt" in low or "dpapi" in low:
        return (f"{browser} encrypts its cookies so they can't be read (app-bound encryption). "
                "Log in to the site in Firefox and choose Firefox for cookies in Settings.")
    if "could not find" in low and "cookies database" in low:
        return f"No {browser} cookies were found (is it installed?). Choose another browser in Settings."
    if "could not copy" in low and "cookie" in low:
        return f"Couldn't read cookies from {browser}. Close it completely and try again."
    if "drm protected" in low or "drm-protected" in low:
        return "This video is DRM-protected and can't be downloaded."
    if "unsupported url" in low:
        return "This link isn't supported. Check the URL or try a direct link to the video."
    if "is not a valid url" in low or "no such file or directory" in low and "http" not in url:
        return "That doesn't look like a valid link."
    if "confirm your age" in low or "age-restricted" in low or "age restricted" in low \
            or "inappropriate for some users" in low:
        return f"This video is age-restricted and requires a signed-in account. {cookie_hint}"
    if "not a bot" in low:
        return f"{site} asked to confirm you're not a bot. {cookie_hint}"
    if "private video" in low or "this video is private" in low or "private account" in low:
        return f"This content is private. {cookie_hint}"
    if ("login required" in low or "requires login" in low or "log in" in low or "login_required" in low
            or "rate-limit reached or login required" in low or "use --cookies" in low
            or "cookies-from-browser" in low or "authentication" in low and "required" in low
            or "sign in" in low):
        return f"{site} requires login for this content. {cookie_hint}"
    if "members-only" in low or "join this channel" in low or "premium" in low and "member" in low:
        return f"This content is for members only. {cookie_hint}"
    if "not available in your country" in low or "geo restrict" in low or "geo-restrict" in low \
            or "blocked it in your country" in low or "not available from your location" in low:
        return "This content is not available in your country (geo-blocked)."
    if "http error 429" in low or "too many requests" in low:
        return f"{site} is rate-limiting requests. Wait a few minutes and try again."
    if "live event will begin" in low or "premieres in" in low or "is upcoming" in low:
        return "This is a scheduled live stream/premiere that hasn't started yet."
    if "video unavailable" in low or "this video is unavailable" in low or "has been removed" in low \
            or "no longer available" in low or "http error 404" in low or "does not exist" in low:
        return "This video is unavailable (it may have been removed or the link is wrong)."
    if "requested format is not available" in low:
        return "The requested quality/format isn't available for this video. Try another quality."
    if "no video formats found" in low or "no media found" in low or "there's no video in this" in low:
        return "No downloadable media was found at this link."
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return "FFmpeg is missing. Run the setup/update again."
    if any(s in low for s in ("getaddrinfo failed", "failed to resolve", "name or service not known",
                              "timed out", "connection reset", "connection refused", "network is unreachable",
                              "unable to download webpage", "unable to download json", "urlopen error",
                              "remote end closed connection", "connection aborted", "ssl:")):
        return "Network error. Check your internet connection and try again."
    if "no space left" in low or "errno 28" in low:
        return "Not enough disk space in the download folder."
    if "permission denied" in low or "errno 13" in low:
        return "Can't write to the download folder (permission denied). Choose another folder."
    msg = re.sub(r"^ERROR:\s*", "", main)
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]*:\s*", "", msg).strip()
    return msg[:300] or "Download failed for an unknown reason."


def normalize_url(url: str) -> str:
    """Clean up a user-supplied URL.

    Adds a missing scheme and rewrites ``vimeo.com/<id>[/<hash>]`` page links to the embeddable
    ``player.vimeo.com`` form, which (unlike the page) usually works without a Vimeo login.
    """
    url = (url or "").strip().strip('"').strip("'")
    has_scheme = re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I)
    if url and not has_scheme and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
        url = "https://" + url
    m = re.match(r"^https?://(?:www\.)?vimeo\.com/(\d+)(?:/([0-9a-f]{6,}))?/?(?:[?#].*)?$", url, re.I)
    if m:
        url = f"https://player.vimeo.com/video/{m.group(1)}" + (f"?h={m.group(2)}" if m.group(2) else "")
    return url


def _base_args(deps: DependencyManager, cookies_browser: str | None) -> list[str]:
    args = [
        str(deps.ytdlp_path),
        "--ignore-config",
        "--color", "never",
        "--encoding", "utf-8",
        "--ffmpeg-location", str(deps.ffmpeg_dir),
        "--socket-timeout", "20",
        "--cache-dir", str(DATA_DIR / "cache"),
    ]
    if deps.deno_path.is_file():
        args += ["--js-runtimes", f"deno:{deps.deno_path}"]
    if cookies_browser:
        args += ["--cookies-from-browser", cookies_browser]
    return args


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _popen(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=CREATE_NO_WINDOW,
        env=_subprocess_env(),
    )


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill ``proc`` and all of its children."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=15, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _require_ready(deps: DependencyManager) -> None:
    if not deps.ytdlp_path.is_file():
        raise EngineError("yt-dlp is not installed yet. Run the setup (or --setup) first.")


# ====================================================================== fetch_info
def _heights_from(info: dict[str, Any]) -> tuple[list[int], bool]:
    formats = info.get("formats") or []
    heights: set[int] = set()
    has_video = False
    for f in formats:
        vcodec = f.get("vcodec")
        if vcodec == "none" or f.get("ext") == "mhtml":
            continue
        h = _res_of(f)
        if h:
            heights.add(h)
            has_video = True
        elif vcodec not in (None, "none"):
            has_video = True
        elif vcodec is None and f.get("acodec") in (None, "") and \
                str(f.get("ext") or "").lower() in ("mp4", "webm", "mkv", "mov", "flv", "3gp", "ts"):
            has_video = True  # codec unknown (e.g. Facebook sd/hd), container says video
    if not formats:
        has_video = info.get("vcodec") != "none" and bool(info.get("height") or info.get("width")
                                                          or info.get("vcodec"))
        if _res_of(info):
            heights.add(_res_of(info))
    return sorted(heights, reverse=True), has_video


def _res_of(f: dict[str, Any]) -> int:
    """Quality label of a format: the smaller dimension (so a 1080x1920 Short is '1080p')."""
    h, w = f.get("height"), f.get("width")
    dims = [int(x) for x in (h, w) if isinstance(x, (int, float)) and x > 0]
    return min(dims) if dims else 0


def _run_json(args: list[str], url: str, cookies_browser: str | None,
              cancel_event: threading.Event | None) -> dict[str, Any]:
    proc = subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW, env=_subprocess_env(),
    )
    result: dict[str, bytes] = {}

    def _comm() -> None:
        out, err = proc.communicate()
        result["out"], result["err"] = out or b"", err or b""

    t = threading.Thread(target=_comm, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.2)
        if cancel_event is not None and cancel_event.is_set():
            _kill_tree(proc)
            t.join(5)
            raise EngineError("Cancelled.")
    out = result.get("out", b"").decode("utf-8", "replace")
    err = result.get("err", b"").decode("utf-8", "replace")
    data: dict[str, Any] | None = None
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
    if data is None:
        raise EngineError(friendly_error(err or out, url, cookies_browser))
    return data


def fetch_info(url: str, deps: DependencyManager, cookies_browser: str | None = None,
               cancel_event: threading.Event | None = None) -> MediaInfo:
    """Fetch metadata for ``url`` (blocking; call from a worker thread).

    Raises:
        EngineError: with a user-friendly message on failure or cancellation.
    """
    url = normalize_url(url)
    if not re.match(r"^https?://", url, re.I):
        raise EngineError("That doesn't look like a valid link.")
    _require_ready(deps)
    base = _base_args(deps, cookies_browser)
    data = _run_json(base + ["-J", "--flat-playlist", "--no-warnings", "--", url], url,
                     cookies_browser, cancel_event)

    is_playlist = data.get("_type") in ("playlist", "multi_video")
    extractor = str(data.get("extractor_key") or data.get("extractor") or "")
    if is_playlist:
        entries = [e for e in (data.get("entries") or []) if e]
        count = int(data.get("playlist_count") or len(entries) or 0)
        if count == 0 and not entries:
            raise EngineError("This playlist is empty, private or unavailable.")
        thumb = _best_thumbnail(data) or (_best_thumbnail(entries[0]) if entries else None)
        heights: list[int] = []
        has_video = True
        if entries:
            first = entries[0]
            if first.get("formats"):
                heights, has_video = _heights_from(first)
            else:
                first_url = first.get("url") or first.get("webpage_url")
                if first_url:
                    try:
                        sub = _run_json(base + ["-J", "--no-playlist", "--no-warnings", "--", first_url],
                                        first_url, cookies_browser, cancel_event)
                        heights, has_video = _heights_from(sub)
                        thumb = thumb or _best_thumbnail(sub)
                    except EngineError as exc:
                        if str(exc) == "Cancelled.":
                            raise
        if not heights and has_video and not extractor.lower().startswith("soundcloud"):
            heights = [2160, 1440, 1080, 720, 480, 360]
        if extractor.lower().startswith("soundcloud"):
            has_video, heights = False, []
        return MediaInfo(
            url=url,
            title=str(data.get("title") or data.get("id") or "Playlist"),
            uploader=data.get("uploader") or data.get("channel"),
            duration=None,
            thumbnail=thumb,
            extractor=extractor,
            webpage_url=str(data.get("webpage_url") or url),
            is_playlist=True,
            entry_count=count,
            heights=heights,
            has_video=has_video,
        )

    heights, has_video = _heights_from(data)
    duration = data.get("duration")
    return MediaInfo(
        url=url,
        title=str(data.get("title") or data.get("id") or "Untitled"),
        uploader=data.get("uploader") or data.get("channel") or data.get("creator"),
        duration=float(duration) if isinstance(duration, (int, float)) else None,
        thumbnail=_best_thumbnail(data),
        extractor=extractor,
        webpage_url=str(data.get("webpage_url") or url),
        is_playlist=False,
        entry_count=1,
        heights=heights,
        has_video=has_video,
    )


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb:
        return thumb
    thumbs = [t for t in (info.get("thumbnails") or []) if isinstance(t, dict) and t.get("url")]
    if not thumbs:
        return None
    # Prefer jpg (Pillow-friendly) and a reasonable size.
    def score(t: dict[str, Any]) -> tuple[int, int]:
        url = t["url"].lower()
        is_jpg = 1 if (".jpg" in url or ".jpeg" in url) else 0
        pref = t.get("preference") or 0
        return is_jpg, pref if isinstance(pref, int) else 0

    return max(thumbs, key=score)["url"]


# ====================================================================== command building
def build_download_args(url: str, options: JobOptions, deps: DependencyManager) -> list[str]:
    """Return the full yt-dlp command line for a download job."""
    kind = options.kind if options.kind in KINDS else "mp4"
    args = _base_args(deps, options.cookies_browser)
    args += [
        "--newline", "--progress", "--no-quiet",
        "--no-mtime", "--windows-filenames",
        "--concurrent-fragments", "4",
        "--progress-template",
        "download:" + _DL_TAG + _SEP.join([
            "%(progress.status)s", "%(progress.downloaded_bytes)s", "%(progress.total_bytes)s",
            "%(progress.total_bytes_estimate)s", "%(progress.speed)s", "%(progress.eta)s",
            "%(progress.fragment_index)s", "%(progress.fragment_count)s",
            "%(info.vcodec)s", "%(info.acodec)s", "%(info.playlist_index)s", "%(info.n_entries)s",
        ]),
        "--progress-template", "postprocess:" + _PP_TAG + "%(progress.status)s" + _SEP
        + "%(progress.postprocessor)s",
        "--print", "after_move:" + _FILE_TAG + "%(filepath)s",
        "-P", options.out_dir or ".",
    ]
    if options.playlist:
        args += ["--yes-playlist", "--ignore-errors",
                 "-o", "%(playlist_title,playlist_id|Playlist).100B/"
                       "%(playlist_index|0)03d - %(title).150B [%(id)s].%(ext)s"]
    else:
        args += ["--no-playlist", "-o", "%(title).150B [%(id)s].%(ext)s"]

    if kind == "mp4":
        q = str(options.quality or "best").strip().lower().removesuffix("p")
        if q != "best" and not (q.isdigit() and int(q) > 0):
            q = "best"
        # Resolution first (exact requested height when it exists; "res" is the smaller dimension so
        # portrait videos/Shorts work), then fps, then codec compatibility (H.264 > VP9 > AV1 at the
        # same resolution), then AAC audio. High resolutions on YouTube are video-only VP9/AV1
        # streams, so we always merge best video + best audio instead of picking a combined format.
        res = "res" if q == "best" else f"res:{int(q)}"
        fmt = "bv*+ba/b"
        sort = f"{res},fps,vcodec:h264,acodec:aac,ext:mp4:m4a"
        args += ["-f", fmt, "-S", sort, "--merge-output-format", "mp4", "--remux-video", "mp4"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    elif kind == "mp3":
        br = options.mp3_bitrate if options.mp3_bitrate in MP3_BITRATES else "320"
        args += ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", f"{br}K"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    else:  # m4a
        args += ["-f", "ba[acodec^=mp4a]/ba[ext=m4a]/ba/b", "-S", "acodec:aac",
                 "-x", "--audio-format", "m4a", "--audio-quality", "256K"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    if options.embed_metadata:
        args += ["--embed-metadata"]
    args += ["--", url]
    return args


def probe_streams(path: str, deps: DependencyManager) -> list[dict[str, Any]]:
    """Return ffprobe stream info (codec_type, codec_name, attached_pic) for ``path``."""
    try:
        proc = subprocess.run(
            [str(deps.ffprobe_path), "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,width,height:stream_disposition=attached_pic", "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
        )
        streams = json.loads(proc.stdout or "{}").get("streams") or []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [
        {
            "type": s.get("codec_type"),
            "codec": s.get("codec_name"),
            "attached_pic": bool((s.get("disposition") or {}).get("attached_pic")),
            "width": s.get("width"),
            "height": s.get("height"),
            "index": s.get("index"),
        }
        for s in streams
    ]


def probe_duration(path: str, deps: DependencyManager) -> float | None:
    """Return the media duration in seconds (None if unknown)."""
    try:
        proc = subprocess.run(
            [str(deps.ffprobe_path), "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# ====================================================================== DownloadJob
class DownloadJob:
    """A single download (video, audio, or playlist) running yt-dlp in a worker thread."""

    def __init__(self, url: str, title: str, options: JobOptions, deps: DependencyManager) -> None:
        self.id: str = uuid.uuid4().hex[:12]
        self.url = url
        self.title = title
        self.options = options
        self.deps = deps
        self.state: str = "queued"
        self.output_path: str | None = None
        self.output_paths: list[str] = []
        self.error: str | None = None

        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._tail: deque[str] = deque(maxlen=60)
        self._touched: set[str] = set()     # files yt-dlp wrote / was writing (for cleanup)
        self._video_ids: set[str] = set()
        self._on_progress: ProgressCb | None = None
        self._on_done: DoneCb | None = None
        self._item = ""
        self._part_count = 1
        self._part_index = 0
        self._part_frac = 0.0
        self._failed_items = 0
        self._expected_items = 0

    # ------------------------------------------------------------ public
    def start(self, on_progress: ProgressCb, on_done: DoneCb) -> None:
        """Start the download in a background thread. Callbacks run on that thread."""
        if self._thread is not None:
            raise RuntimeError("job already started")
        self._on_progress, self._on_done = on_progress, on_done
        self._thread = threading.Thread(target=self._run, name=f"dl-{self.id}", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop the download, kill the yt-dlp process tree and remove partial files."""
        if self.state in ("done", "error", "cancelled"):
            return
        self._cancelled.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            _kill_tree(proc)
        if self._thread is None:  # never started
            self.state = "cancelled"
            self._cleanup_partials()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the worker finishes. Returns True if it finished."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    # ------------------------------------------------------------ worker
    def _emit(self, fraction: float | None, status: str, message: str,
              percent: str = "", speed: str = "", eta: str = "") -> None:
        if self._on_progress is None or self._cancelled.is_set():
            return
        if not percent and fraction is not None:
            percent = f"{fraction * 100:.1f}%"
        try:
            self._on_progress(self, {
                "fraction": fraction, "percent": percent, "speed": speed, "eta": eta,
                "status": status, "message": message, "item": self._item,
            })
        except Exception:  # noqa: BLE001 - never let UI errors kill the worker
            pass

    def _finish(self, ok: bool, message: str) -> None:
        if self._on_done is None:
            return
        try:
            self._on_done(self, ok, message)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        try:
            ok, msg = self._run_inner()
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            ok, msg = False, f"Unexpected error: {exc}"
        if self._cancelled.is_set():
            self.state = "cancelled"
            self._cleanup_partials()
            self._finish(False, "Cancelled.")
            return
        self.state = "done" if ok else "error"
        if not ok:
            self.error = msg
        self._finish(ok, msg)

    def _run_inner(self) -> tuple[bool, str]:
        try:
            _require_ready(self.deps)
        except EngineError as exc:
            return False, str(exc)
        out_dir = self.options.out_dir or str(Path.home() / "Downloads")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            return False, "Can't create the download folder. Choose another folder in Settings."
        self.options.out_dir = out_dir

        args = build_download_args(normalize_url(self.url), self.options, self.deps)
        self.state = "downloading"
        self._emit(None, "downloading", "Starting...")
        try:
            proc = _popen(args)
        except OSError as exc:
            return False, f"Could not start yt-dlp: {exc}"
        with self._lock:
            self._proc = proc
        if self._cancelled.is_set():
            _kill_tree(proc)

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            self._handle_line(line)
        proc.wait()
        if self._cancelled.is_set():
            return False, "Cancelled."

        files = [p for p in self.output_paths if os.path.isfile(p)]
        if not files and self.output_paths:
            files = self._find_outputs()
        if proc.returncode != 0 and not files:
            return False, friendly_error(list(self._tail), self.url, self.options.cookies_browser)
        if not files:
            if any("has already been downloaded" in ln for ln in self._tail):
                return True, self.output_path or "Already downloaded."
            return False, friendly_error(list(self._tail), self.url, self.options.cookies_browser)

        if self.options.kind == "mp4":
            for i, path in enumerate(files):
                if self._cancelled.is_set():
                    return False, "Cancelled."
                self._ensure_mp4_compat(path, i, len(files))
        self.output_path = files[-1] if len(files) == 1 else os.path.dirname(files[-1])
        if self.options.playlist:
            failed = max(self._expected_items - len(files), 0) if self._expected_items else self._failed_items
            if failed:
                return True, (f"Finished with errors: {failed} of {self._expected_items or '?'} item(s) "
                              f"failed. Saved to {self.output_path}")
        # A non-zero exit with the final file present means a non-fatal post-processing step
        # (e.g. thumbnail embedding) failed; the media itself is fine.
        return True, self.output_path

    # ------------------------------------------------------------ output parsing
    def _handle_line(self, line: str) -> None:
        if line.startswith(_DL_TAG):
            self._handle_progress(line[len(_DL_TAG):].split(_SEP))
            return
        if line.startswith(_PP_TAG):
            status, _, name = line[len(_PP_TAG):].partition(_SEP)
            if status == "started" and name not in ("MoveFiles", "Concat"):
                if name == "ThumbnailsConvertor" and self._part_index == 0:
                    # runs before the media download starts; not a real processing phase
                    self._emit(None, "downloading", "Preparing...")
                    return
                self.state = "processing"
                self._emit(None, "processing", _PP_LABELS.get(name, f"Processing ({name})..."))
            return
        if line.startswith(_FILE_TAG):
            path = line[len(_FILE_TAG):].strip()
            if path:
                self.output_paths.append(path)
                self.output_path = path
            return

        self._tail.append(line)
        m = re.match(r"^\[download\] Destination: (.+)$", line)
        if m:
            self._touched.add(m.group(1).strip())
            if self.state != "downloading":
                self.state = "downloading"
            return
        m = re.match(r"^\[download\] Downloading (?:item|video) (\d+) of (\d+)", line)
        if m:
            self._item = f"{m.group(1)}/{m.group(2)}"
            self._expected_items = int(m.group(2))
            self._part_index = 0
            self.state = "downloading"
            self._emit(0.0, "downloading", f"Starting item {self._item}...")
            return
        m = re.match(r"^\[info\] ([^:\s]+): Downloading \d+ format\(s\): (\S+)", line)
        if m:
            self._video_ids.add(m.group(1))
            self._part_count = m.group(2).count("+") + 1
            self._part_index = 0
            self._part_frac = 0.0
            return
        m = re.match(r"^\[info\] Writing video thumbnail .* to: (.+)$", line)
        if m:
            self._touched.add(m.group(1).strip())
            return
        m = re.match(r'^\[Merger\] Merging formats into "(.+)"$', line)
        if m:
            self._touched.add(m.group(1))
        pm = _PP_LINE_RE.match(line)
        if pm and not (pm.group(1) == "ThumbnailsConvertor" and self._part_index == 0):
            self.state = "processing"
            self._emit(None, "processing", _PP_LABELS[pm.group(1)])
            return
        if line.startswith("ERROR:") and self.options.playlist and "processing:" not in line:
            self._failed_items += 1

    def _handle_progress(self, parts: list[str]) -> None:
        if len(parts) < 12:
            return
        (status, done, total, total_est, speed, eta, frag_i, frag_n,
         vcodec, _acodec, pl_index, n_entries) = parts[:12]
        if pl_index not in ("NA", "", "None") and n_entries not in ("NA", "", "None"):
            self._item = f"{pl_index}/{n_entries}"
        self.state = "downloading"

        def num(s: str) -> float | None:
            try:
                return float(s)
            except ValueError:
                return None

        d, t_exact, t_est = num(done), num(total), num(total_est)
        fi, fn = num(frag_i), num(frag_n)
        frac: float | None = None
        if status == "finished":
            frac = 1.0
        elif d is not None and t_exact:
            frac = min(d / t_exact, 1.0)
        else:
            if d is not None and t_est:
                frac = min(d / t_est, 0.99)
            if fn and fi is not None:
                cap = min((fi + 1) / fn, 0.99)
                frac = cap if frac is None else min(frac, cap)
        # never move backwards within one stream
        if frac is not None:
            frac = max(frac, self._part_frac)
            self._part_frac = 0.0 if status == "finished" else frac

        is_audio = vcodec == "none"
        part_label = "audio" if is_audio else "video"
        if self.options.kind in ("mp3", "m4a"):
            part_label = "audio"
        # Merge per-stream progress into one overall bar (video ~85 %, audio ~15 %).
        overall = frac
        if frac is not None and self._part_count > 1:
            if is_audio:
                overall = 0.85 + 0.15 * frac
            else:
                overall = 0.85 * frac
        if status == "finished":
            self._part_index += 1

        sp = num(speed)
        speed_s = _fmt_bytes(sp) + "/s" if sp else ""
        et = num(eta)
        eta_s = _fmt_eta(et) if et is not None else ""
        size_s = f" of {_fmt_bytes(t_exact)}" if t_exact else (f" of ~{_fmt_bytes(t_est)}" if t_est else "")
        msg = f"Downloading {part_label}{size_s}"
        self._emit(overall, "downloading", msg,
                   percent=f"{overall * 100:.1f}%" if overall is not None else "",
                   speed=speed_s, eta=eta_s)

    def _find_outputs(self) -> list[str]:
        """Fallback when printed paths don't match disk (encoding quirks): search by video id."""
        exts = {"mp4": (".mp4", ".mkv", ".webm"), "mp3": (".mp3",), "m4a": (".m4a",)}[self.options.kind]
        found: list[str] = []
        for vid in self._video_ids:
            pattern = os.path.join(glob.escape(self.options.out_dir), "**", f"*[[]{glob.escape(vid)}[]]*")
            found += [p for p in glob.glob(pattern, recursive=True) if p.lower().endswith(exts)
                      and not re.search(r"\.f\d+[\w-]*\.\w+$", p)]
        return sorted(set(found), key=os.path.getmtime)

    # ------------------------------------------------------------ post-checks
    def _ensure_mp4_compat(self, path: str, index: int, total: int) -> None:
        """Make an MP4 play in Windows' built-in player.

        * audio that isn't AAC/MP3/ALAC (e.g. Opus) is re-encoded to AAC (video is copied);
        * HEVC/H.265 video (needs a paid codec extension on many PCs; common on TikTok) is
          re-encoded to H.264 at the same resolution.
        Resolution is never changed. Failures leave the original file untouched.
        """
        if not path.lower().endswith(".mp4") or not self.deps.ffprobe_path.is_file():
            return
        streams = probe_streams(path, self.deps)
        audio = [s for s in streams if s["type"] == "audio"]
        video = [s for s in streams if s["type"] == "video" and not s["attached_pic"]]
        covers = [s for s in streams if s["type"] == "video" and s["attached_pic"]]
        fix_audio = bool(audio) and not all(s["codec"] in _MP4_OK_AUDIO for s in audio)
        fix_video = any(s["codec"] in _MP4_REENCODE_VIDEO for s in video)
        if not (fix_audio or fix_video):
            return
        self.state = "processing"
        suffix = f" ({index + 1}/{total})" if total > 1 else ""
        what = "video to H.264" if fix_video else "audio to AAC"
        label = f"Converting {what} for compatibility{suffix}..."
        self._emit(None, "processing", label)
        tmp = path[:-4] + ".compat-tmp.mp4"
        self._touched.add(tmp)
        args = [str(self.deps.ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostats",
                "-progress", "pipe:1", "-y", "-i", path, "-map", "0:V"]
        if audio:
            args += ["-map", "0:a"]
        if covers and fix_video:
            args += ["-map", f"0:{covers[0]['index']}"]
        args += ["-map_metadata", "0", "-map_chapters", "0", "-c", "copy"]
        if fix_video:
            args += ["-c:v:0", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p"]
            if covers:
                args += ["-c:v:1", "copy", "-disposition:v:1", "attached_pic"]
        if fix_audio:
            args += ["-c:a", "aac", "-b:a", _AAC_BITRATE]
        args += ["-movflags", "+faststart", tmp]
        duration = probe_duration(path, self.deps)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    creationflags=CREATE_NO_WINDOW)
            with self._lock:
                self._proc = proc  # type: ignore[assignment]
            err_chunks: list[str] = []
            t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read() if proc.stderr else ""),
                                 daemon=True)
            t.start()
            assert proc.stdout is not None
            for line in proc.stdout:
                key, _, val = line.strip().partition("=")
                if key == "out_time_us" and duration and val.isdigit():
                    frac = min(int(val) / 1e6 / duration, 1.0)
                    self._emit(frac, "processing", label, percent=f"{frac * 100:.0f}%")
            proc.wait()
            t.join(5)
            if proc.returncode == 0 and os.path.getsize(tmp) > 0 and not self._cancelled.is_set():
                os.replace(tmp, path)
            elif not self._cancelled.is_set():
                self._tail.append("".join(err_chunks)[-300:])
        except OSError:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ------------------------------------------------------------ cleanup
    def _cleanup_partials(self) -> None:
        """Remove .part/.ytdl/intermediate files that belong to this job."""
        candidates: set[str] = set()
        final = set(self.output_paths)
        for path in self._touched:
            for suffix in (".part", ".ytdl", ".temp", ""):
                candidates.add(path + suffix)
            candidates.update(glob.glob(glob.escape(path) + ".part-Frag*"))
            candidates.update(glob.glob(glob.escape(path) + ".part*"))
            root, ext = os.path.splitext(path)
            candidates.add(f"{root}.temp{ext}")
            if ext.lower() in (".webp", ".jpg", ".jpeg", ".png"):  # thumbnail (may be converted)
                candidates.update(f"{root}{e}" for e in (".webp", ".jpg", ".jpeg", ".png"))
        out_dir = self.options.out_dir
        if out_dir and os.path.isdir(out_dir):
            for vid in self._video_ids:
                pattern = os.path.join(glob.escape(out_dir), "**", f"*[[]{glob.escape(vid)}[]]*")
                for p in glob.glob(pattern, recursive=True):
                    if re.search(r"\.(part|ytdl|jpg|jpeg|png|webp)$|\.part-Frag\d+|\.f\d+[\w-]*\.\w+$"
                                 r"|\.temp\.\w+$", p, re.I):
                        candidates.add(p)
        for p in candidates:
            if p in final and not p.endswith((".part", ".ytdl")):
                continue  # keep fully finished playlist items
            is_partial = bool(re.search(r"\.(part|ytdl|temp)$|\.part-Frag\d+|\.temp\.\w+$|\.compat-tmp\.mp4$", p))
            is_intermediate = bool(re.search(r"\.f\d+[\w-]*\.\w+$", p)) or \
                p.lower().endswith((".webp", ".jpg", ".png")) or p in self._touched
            if not (is_partial or is_intermediate):
                continue
            for _ in range(10):  # the killed process may still hold a handle for a moment
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                    break
                except PermissionError:
                    time.sleep(0.3)
                except OSError:
                    break


def _fmt_bytes(n: float | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"
