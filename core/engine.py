"""Download engine: drives the standalone ``yt-dlp.exe`` as a subprocess.

Public API (see CONTRACT.md): :class:`MediaInfo`, :class:`EngineError`, :func:`fetch_info`,
:class:`JobOptions`, :class:`DownloadJob`.
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
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
_INFO_TAG = "[H190K-INFO]"

# Per-job scratch folders live inside the download folder (same volume, so the final move is a
# cheap rename) under this hidden directory: <out_dir>/.h190k-tmp/<job id>/
TEMP_DIR_NAME = ".h190k-tmp"
_STALE_TEMP_SECONDS = 3 * 24 * 3600
# Common audio bitrates; a probed bitrate within 5 % of one of these is labelled with it
# (YouTube's "128k" AAC probes as ~129.5 kbps).
_STD_KBPS = (32, 48, 64, 96, 112, 128, 160, 192, 224, 256, 320)
# HTTP 403 recovery: re-extract (fresh signed URLs) this many times, then also skip the refused
# stream and alternate the YouTube player client, up to _MAX_403_RETRIES retries in total.
_FRESH_403_RETRIES = 2
_MAX_403_RETRIES = 5
_ALT_YT_CLIENT = "web_embedded"  # full format list (up to 4K) without a PO token (yt-dlp 2026.08)
_INTERMEDIATE_RE = re.compile(r"\.f\d+[\w-]*\.\w+$|\.(part|ytdl|temp)$|\.part-Frag\d+|\.temp\.\w+$"
                              r"|\.compat-tmp\.mp4$", re.IGNORECASE)

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
_PP_LINE_RE = re.compile(r"^\[({})\]".format("|".join(re.escape(k) for k in _PP_LABELS)))


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


#: ``DownloadJob.error_kind`` values (the UI picks an action from these).
ERROR_KINDS = ("login_required", "age_restricted", "members_only", "private", "http_403",
               "unsupported_url", "network", "geo_blocked", "drm", "unavailable", "error")

_UPDATE_HINT = "About & updates → Update all"
_COOKIES_HINT = "Settings → Sign-in & cookies (Firefox recommended)"


def http_403_message(site: str = "YouTube", cause: str = "refused", working: str | None = None) -> str:
    """Short, non-technical explanation of an HTTP 403 plus what to try, in order.

    ``cause``: ``"refused"`` (every stream was refused, often a temporary block of the connection),
    ``"expired"`` (the link stopped working partway through a long download) or ``"quality"``
    (only the chosen quality is blocked; ``working`` names one that downloads).
    """
    if cause == "expired":
        what = (f"{site} stopped sending the file partway through (the download link expired - "
                "this can happen on long videos or slow connections).")
    elif cause == "quality":
        what = f"{site} is blocking this quality of the video right now."
    else:
        what = (f"{site} refused to send this video, even after retrying with other streams. "
                "This is usually a temporary block of your connection.")
    if working:
        first = f"try {working}"
    elif cause == "expired":
        first = "try again"
    else:
        first = "try another quality"
    return (f"{what}\nWhat to try: {first}; update the tools ({_UPDATE_HINT}); try again later or "
            f"on another network (VPN off/on); or sign in via {_COOKIES_HINT}.\n(HTTP 403)")


def diagnose_error(lines: list[str] | str, url: str = "",
                   cookies_browser: str | None = None) -> tuple[str, str]:
    """Classify yt-dlp output: returns ``(error_kind, user-friendly message)``.

    ``error_kind`` is one of :data:`ERROR_KINDS`.
    """
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

    browser = cookies_browser.title() if cookies_browser else None
    if "failed to decrypt" in low or "dpapi" in low:
        return "login_required", (
            f"{browser or 'The browser'} encrypts its cookies so they can't be read (app-bound "
            "encryption). Log in to the site in Firefox and choose Firefox for cookies in Settings.")
    if "could not find" in low and "cookies database" in low:
        return "login_required", (
            f"No {browser} cookies were found (is it installed?). Choose another browser in Settings."
            if browser else
            "No browser cookies were found (is the browser installed?). Choose another browser in Settings.")
    if "could not copy" in low and "cookie" in low:
        return "login_required", (f"Couldn't read cookies from {browser or 'the browser'}. "
                                  "Close it completely and try again.")
    if "drm protected" in low or "drm-protected" in low:
        return "drm", "This video is DRM-protected and can't be downloaded."
    if "unsupported url" in low:
        return "unsupported_url", "This link isn't supported. Check the URL or try a direct link to the video."
    if "is not a valid url" in low or "no such file or directory" in low and "http" not in url:
        return "unsupported_url", "That doesn't look like a valid link."
    if "confirm your age" in low or "age-restricted" in low or "age restricted" in low \
            or "inappropriate for some users" in low:
        return "age_restricted", f"This video is age-restricted and requires a signed-in account. {cookie_hint}"
    if "not a bot" in low:
        return "login_required", f"{site} asked to confirm you're not a bot. {cookie_hint}"
    if "private video" in low or "this video is private" in low or "private account" in low:
        return "private", f"This content is private. {cookie_hint}"
    if ("login required" in low or "requires login" in low or "log in" in low or "login_required" in low
            or "rate-limit reached or login required" in low or "use --cookies" in low
            or "cookies-from-browser" in low or "authentication" in low and "required" in low
            or "sign in" in low):
        return "login_required", f"{site} requires login for this content. {cookie_hint}"
    if "members-only" in low or "join this channel" in low or "premium" in low and "member" in low:
        return "members_only", f"This content is for members only. {cookie_hint}"
    if "available in your country" in low or "geo restrict" in low or "geo-restrict" in low \
            or "blocked it in your country" in low or "not available from your location" in low:
        return "geo_blocked", "This content is not available in your country (geo-blocked)."
    if "http error 429" in low or "too many requests" in low:
        return "error", f"{site} is rate-limiting requests. Wait a few minutes and try again."
    if "http error 403" in low or "403: forbidden" in low:
        return "http_403", http_403_message(site)
    if "live event will begin" in low or "premieres in" in low or "is upcoming" in low:
        return "unavailable", "This is a scheduled live stream/premiere that hasn't started yet."
    if "video unavailable" in low or "this video is unavailable" in low or "has been removed" in low \
            or "no longer available" in low or "http error 404" in low or "does not exist" in low:
        return "unavailable", "This video is unavailable (it may have been removed or the link is wrong)."
    if "requested format is not available" in low:
        return "error", "The requested quality/format isn't available for this video. Try another quality."
    if "no video formats found" in low or "no media found" in low or "there's no video in this" in low:
        return "unavailable", "No downloadable media was found at this link."
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return "error", "FFmpeg is missing. Run the setup/update again."
    if any(s in low for s in ("getaddrinfo failed", "failed to resolve", "name or service not known",
                              "timed out", "connection reset", "connection refused", "network is unreachable",
                              "unable to download webpage", "unable to download json", "urlopen error",
                              "remote end closed connection", "connection aborted", "ssl:")):
        return "network", "Network error. Check your internet connection and try again."
    if "no space left" in low or "errno 28" in low:
        return "error", "Not enough disk space in the download folder."
    if "permission denied" in low or "errno 13" in low:
        return "error", "Can't write to the download folder (permission denied). Choose another folder."
    msg = re.sub(r"^ERROR:\s*", "", main)
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]*:\s*", "", msg).strip()
    return "error", msg[:300] or "Download failed for an unknown reason."


def friendly_error(lines: list[str] | str, url: str = "", cookies_browser: str | None = None) -> str:
    """Translate yt-dlp stderr output into a short, actionable message."""
    return diagnose_error(lines, url, cookies_browser)[1]


def normalize_url(url: str) -> str:
    """Clean up a user-supplied URL.

    Adds a missing scheme and rewrites ``vimeo.com/<id>[/<hash>]`` page links to the embeddable
    ``player.vimeo.com`` form, which (unlike the page) usually works without a Vimeo login.
    """
    url = (url or "").strip().strip('"').strip("'")
    has_scheme = re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE)
    if url and not has_scheme and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.IGNORECASE):
        url = "https://" + url
    m = re.match(r"^https?://(?:www\.)?vimeo\.com/(\d+)(?:/([0-9a-f]{6,}))?/?(?:[?#].*)?$", url, re.IGNORECASE)
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
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False,
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
    if not re.match(r"^https?://", url, re.IGNORECASE):
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


# ====================================================================== naming
def _norm_quality(quality: str | None) -> str:
    """``"720p"``/``"720"`` -> ``"720"``; anything invalid -> ``"best"``."""
    q = str(quality or "best").strip().lower().removesuffix("p")
    return q if q.isdigit() and int(q) > 0 else "best"


def requested_label(options: JobOptions) -> str:
    """File-name quality label used while downloading (before the real quality is known).

    MP4: ``"720p"`` or ``"best"``; MP3: ``"320kbps"``; M4A: ``"m4a"``. After the download the
    engine renames MP4/M4A files to the delivered quality (see :attr:`DownloadJob.delivered_quality`).
    """
    kind = options.kind if options.kind in KINDS else "mp4"
    if kind == "mp4":
        q = _norm_quality(options.quality)
        return "best" if q == "best" else f"{int(q)}p"
    if kind == "mp3":
        return f"{_mp3_bitrate(options)}kbps"
    return "m4a"


def _mp3_bitrate(options: JobOptions) -> str:
    return options.mp3_bitrate if options.mp3_bitrate in MP3_BITRATES else "320"


def output_template(options: JobOptions) -> str:
    """yt-dlp ``-o`` template: ``<title> [<id>] - <label>.<ext>`` (playlists: in a sub-folder)."""
    suffix = f" - {requested_label(options)}.%(ext)s"
    if options.playlist:
        return ("%(playlist_title,playlist_id|Playlist).100B/"
                "%(playlist_index|0)03d - %(title).150B [%(id)s]" + suffix)
    return "%(title).150B [%(id)s]" + suffix


def _kbps_label(bits_per_second: float | None) -> str | None:
    if not bits_per_second or bits_per_second <= 0:
        return None
    kbps = bits_per_second / 1000
    for std in _STD_KBPS:
        if abs(kbps - std) <= std * 0.05:
            return f"{std}kbps"
    return f"{round(kbps)}kbps"


# ====================================================================== command building
def _exclude_formats(selector: str, format_ids: Sequence[str]) -> str:
    """Add ``[format_id!~='^ID$']`` filters for ``format_ids`` to every atom of ``selector``.

    (A plain ``[format_id!=140]`` is parsed by yt-dlp as a *numeric* filter and never matches.)
    """
    if not format_ids:
        return selector
    filt = "".join(f"[format_id!~='^{re.escape(i)}$']" for i in format_ids)
    return "/".join("+".join(atom + filt for atom in alt.split("+")) for alt in selector.split("/"))


def build_download_args(url: str, options: JobOptions, deps: DependencyManager,
                        temp_dir: str | None = None, force_overwrites: bool = False,
                        exclude_formats: Sequence[str] = (),
                        player_client: str | None = None) -> list[str]:
    """Return the full yt-dlp command line for a download job.

    Args:
        temp_dir: per-job scratch folder for ``.part``/``.fNNN``/thumbnail files. The finished
            file is moved from there to ``options.out_dir`` by yt-dlp.
        force_overwrites: re-download even if the final file already exists.
        exclude_formats: format ids never to pick (streams the site refused with HTTP 403).
        player_client: YouTube player client to extract with (e.g. ``"web_embedded"``).
    """
    kind = options.kind if options.kind in KINDS else "mp4"
    args = _base_args(deps, options.cookies_browser)
    args += [
        "--newline", "--progress", "--no-quiet", "--no-simulate",
        "--no-mtime", "--windows-filenames",
        "--concurrent-fragments", "4",
        "--progress-template",
        "download:" + _DL_TAG + _SEP.join([
            "%(progress.status)s", "%(progress.downloaded_bytes)s", "%(progress.total_bytes)s",
            "%(progress.total_bytes_estimate)s", "%(progress.speed)s", "%(progress.eta)s",
            "%(progress.fragment_index)s", "%(progress.fragment_count)s",
            "%(info.vcodec)s", "%(info.acodec)s", "%(info.playlist_index)s", "%(info.n_entries)s",
            "%(info.format_id)s",
        ]),
        "--progress-template", "postprocess:" + _PP_TAG + "%(progress.status)s" + _SEP
        + "%(progress.postprocessor)s",
        "--print", "before_dl:" + _INFO_TAG + _SEP.join(["%(id)s", "%(duration)s", "%(vcodec)s",
                                                          "%(width)s", "%(height)s", "%(acodec)s"]),
        "--print", "after_move:" + _FILE_TAG + "%(id)s" + _SEP + "%(filepath)s",
    ]
    if temp_dir:
        # Both absolute: yt-dlp resolves a relative "temp:" path against "home".
        args += ["-P", "home:" + os.path.abspath(options.out_dir or "."),
                 "-P", "temp:" + os.path.abspath(temp_dir)]
    else:
        args += ["-P", options.out_dir or "."]
    if force_overwrites:
        args += ["--force-overwrites"]
    if player_client:
        args += ["--extractor-args", f"youtube:player_client={player_client}"]
    args += ["--yes-playlist", "--ignore-errors"] if options.playlist else ["--no-playlist"]
    args += ["-o", output_template(options)]

    if kind == "mp4":
        q = _norm_quality(options.quality)
        # Resolution first (exact requested height when it exists; "res" is the smaller dimension so
        # portrait videos/Shorts work), then fps, then codec compatibility (H.264 > VP9 > AV1 at the
        # same resolution), then AAC audio. High resolutions on YouTube are video-only VP9/AV1
        # streams, so we always merge best video + best audio instead of picking a combined format.
        res = "res" if q == "best" else f"res:{int(q)}"
        fmt = "bv*+ba/b"
        sort = f"{res},fps,vcodec:h264,acodec:aac,ext:mp4:m4a"
        args += ["-f", _exclude_formats(fmt, exclude_formats), "-S", sort, "--merge-output-format", "mp4", "--remux-video", "mp4"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    elif kind == "mp3":
        br = _mp3_bitrate(options)
        args += ["-f", _exclude_formats("ba/b", exclude_formats), "-x", "--audio-format", "mp3", "--audio-quality", f"{br}K"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    else:  # m4a
        args += ["-f", _exclude_formats("ba[acodec^=mp4a]/ba[ext=m4a]/ba/b", exclude_formats),
                 "-S", "acodec:aac",
                 "-x", "--audio-format", "m4a", "--audio-quality", "256K"]
        if options.embed_thumbnail:
            args += ["--embed-thumbnail"]
    if options.embed_metadata:
        args += ["--embed-metadata"]
    args += ["--", url]
    return args


def _probe_media(path: str, deps: DependencyManager) -> dict[str, Any] | None:
    """Probe ``path`` with ffprobe.

    Returns ``{"streams": [...], "duration": float | None}`` (``streams`` is empty when ffprobe
    can't read the file), or ``None`` when ffprobe itself is unavailable / couldn't run.
    """
    if not deps.ffprobe_path.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(deps.ffprobe_path), "-v", "error", "-show_entries",
             ("stream=index,codec_type,codec_name,width,height,bit_rate:stream_disposition=attached_pic"
              ":format=duration,bit_rate"), "-of", "json", path],
            capture_output=True, check=False, text=True, encoding="utf-8", errors="replace", timeout=120,
            creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    fmt = data.get("format") or {}

    def num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    streams = [
        {
            "type": s.get("codec_type"),
            "codec": s.get("codec_name"),
            "attached_pic": bool((s.get("disposition") or {}).get("attached_pic")),
            "width": s.get("width"),
            "height": s.get("height"),
            "index": s.get("index"),
            "bit_rate": num(s.get("bit_rate")),
        }
        for s in (data.get("streams") or [])
    ]
    return {"streams": streams, "duration": num(fmt.get("duration"))}


def probe_streams(path: str, deps: DependencyManager) -> list[dict[str, Any]]:
    """Return ffprobe stream info (type, codec, attached_pic, width, height, index, bit_rate)."""
    media = _probe_media(path, deps)
    return media["streams"] if media else []


def probe_duration(path: str, deps: DependencyManager) -> float | None:
    """Return the media duration in seconds (None if unknown)."""
    media = _probe_media(path, deps)
    return media["duration"] if media else None


def _hide_dir(path: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)  # type: ignore[attr-defined]
        if attrs != -1 and not attrs & FILE_ATTRIBUTE_HIDDEN:
            ctypes.windll.kernel32.SetFileAttributesW(path, attrs | FILE_ATTRIBUTE_HIDDEN)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass


def _remove_tree(path: str) -> None:
    """``rmtree`` that waits a little for handles held by a just-killed process."""
    for _ in range(15):
        if not os.path.exists(path):
            return
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.4)


def _remove_file(path: str) -> None:
    for _ in range(10):  # the killed process may still hold a handle for a moment
        try:
            if os.path.isfile(path):
                os.remove(path)
            return
        except PermissionError:
            time.sleep(0.3)
        except OSError:
            return


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# Final files produced by running jobs of this process (normcased path -> job id). Placing a file
# (relabel / rescue move) happens under _OUTPUT_LOCK, so a job never replaces a file another job
# owns: two jobs that end up with the same quality share one file.
_OUTPUT_LOCK = threading.Lock()
_CLAIMS: dict[str, str] = {}


def _claim(path: str, job_id: str) -> None:
    with _OUTPUT_LOCK:
        _CLAIMS.setdefault(os.path.normcase(os.path.abspath(path)), job_id)


def _claimed_by_other(path: str, job_id: str) -> bool:
    with _OUTPUT_LOCK:
        owner = _CLAIMS.get(os.path.normcase(os.path.abspath(path)))
    return owner is not None and owner != job_id


def _release_claims(job_id: str) -> None:
    with _OUTPUT_LOCK:
        for key in [k for k, v in _CLAIMS.items() if v == job_id]:
            del _CLAIMS[key]


def _res_name(res: int) -> str:
    return {4320: "8K", 2160: "4K"}.get(res, f"{res}p")


# ====================================================================== DownloadJob
class DownloadJob:
    """A single download (video, audio, or playlist) running yt-dlp in a worker thread.

    Every job downloads into its own scratch folder (``<out_dir>/.h190k-tmp/<id>``), so two jobs
    for the same video never share ``.part``/``.fNNN``/thumbnail files; the folder is removed when
    the job ends (success, failure or cancel). Finished files are named
    ``<title> [<id>] - <quality>.<ext>`` where ``<quality>`` is the *delivered* quality
    (``720p``, ``2160p``, ``320kbps``, ``128kbps``...), also exposed as :attr:`delivered_quality`.
    """

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
        #: Quality of the saved file(s): "720p", "2160p", "320kbps", "128kbps"... (None until done).
        #: For playlists with mixed qualities: the highest one.
        self.delivered_quality: str | None = None
        #: Non-fatal problem on a successful job (e.g. thumbnail couldn't be embedded).
        self.warning: str | None = None
        #: True when the file already existed and was reused instead of downloaded again.
        self.already_downloaded: bool = False
        #: This job's scratch folder (removed when the job ends).
        self.temp_dir: str | None = None
        #: On failure: one of ERROR_KINDS ("http_403", "login_required", ...); None otherwise.
        self.error_kind: str | None = None
        #: On failure: a quality known to work for this video ("720p"), if the engine found one.
        self.suggested_quality: str | None = None

        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._tail: deque[str] = deque(maxlen=60)
        self._touched: set[str] = set()     # files yt-dlp wrote / was writing (for cleanup)
        self._video_ids: set[str] = set()
        self._items: dict[str, dict[str, Any]] = {}   # video id -> {"duration", "vcodec"}
        self._printed: list[tuple[str, str]] = []     # (video id, final path) from yt-dlp
        self._existing: set[str] = set()              # "has already been downloaded" paths
        self._fmt_list: list[str] = []                # format ids of the current download
        self._fmt_done: set[str] = set()              # ... that finished downloading
        self._fmt_bytes: dict[str, float] = {}        # ... bytes received per format id
        self._saw_403 = False
        self._excluded: list[str] = []                # format ids refused with HTTP 403
        self._planned: dict[str, int] = {}            # video id -> resolution first selected
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
        with contextlib.suppress(Exception):  # never let UI errors kill the worker
            self._on_progress(self, {
                "fraction": fraction, "percent": percent, "speed": speed, "eta": eta,
                "status": status, "message": message, "item": self._item,
            })

    def _finish(self, ok: bool, message: str) -> None:
        if self._on_done is None:
            return
        with contextlib.suppress(Exception):
            self._on_done(self, ok, message)

    def _run(self) -> None:
        try:
            ok, msg = self._run_inner()
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            ok, msg = False, f"Unexpected error: {exc}"
        with contextlib.suppress(Exception):  # cleanup must never change the outcome
            self._cleanup_partials()
        _release_claims(self.id)
        if self._cancelled.is_set():
            self.state = "cancelled"
            self.error_kind = None
            self._finish(False, "Cancelled.")
            return
        self.state = "done" if ok else "error"
        if ok:
            self.error_kind = None
        else:
            self.error = msg
            self.error_kind = self.error_kind or "error"
        self._finish(ok, msg)

    def _run_inner(self) -> tuple[bool, str]:
        try:
            _require_ready(self.deps)
        except EngineError as exc:
            return False, str(exc)
        # Absolute: yt-dlp resolves a relative "temp:" path against "home".
        out_dir = os.path.abspath(self.options.out_dir or str(Path.home() / "Downloads"))
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            return False, "Can't create the download folder. Choose another folder in Settings."
        self.options.out_dir = out_dir
        self.temp_dir = self._make_temp_dir(out_dir)

        url = normalize_url(self.url)
        is_youtube = _site_name(None, url) == "YouTube"
        force = False
        client: str | None = None
        retries_403 = 0
        while True:
            ok, msg, action = self._attempt(url, force, client)
            if ok or self._cancelled.is_set():
                return ok, msg
            if action == "overwrite" and not force:
                # The file on disk claimed "already downloaded" but is damaged: download it again.
                force = True
            elif action == "403" and retries_403 < _MAX_403_RETRIES:
                retries_403 += 1
                failing, partway = self._failing_format()
                # 1-2: fresh extraction = new signed URLs (the .part in the scratch folder resumes).
                # Then: skip the refused stream (-> same resolution in another codec, then lower)
                # and alternate the YouTube player client.
                if retries_403 > _FRESH_403_RETRIES and failing and not partway:
                    if failing not in self._excluded:
                        self._excluded.append(failing)
                    if is_youtube:
                        client = _ALT_YT_CLIENT if client is None else None
                self._emit(None, "downloading",
                           f"The site refused the stream - retrying ({retries_403}/{_MAX_403_RETRIES})...")
                if self._cancelled.wait(min(1.5 * retries_403, 5.0)):
                    return False, "Cancelled."
            else:
                return ok, msg
            self._reset_attempt()

    def _reset_attempt(self) -> None:
        self._printed.clear()
        self._existing.clear()
        self._items.clear()
        self._tail.clear()
        self._fmt_list, self._fmt_done, self._fmt_bytes = [], set(), {}
        self._saw_403 = False
        self.output_paths = []
        self.output_path = None
        self.warning = None
        self.error_kind = None
        self.already_downloaded = False
        self._failed_items = self._expected_items = 0
        self._part_index, self._part_count, self._part_frac = 0, 1, 0.0

    def _failing_format(self) -> tuple[str | None, bool]:
        """(format id that was being downloaded when the attempt failed, had it received data?)."""
        for fid in self._fmt_list:
            if fid not in self._fmt_done:
                return fid, self._fmt_bytes.get(fid, 0) > 0
        return None, False

    def _attempt(self, url: str, force_overwrites: bool,
                 client: str | None = None) -> tuple[bool, str, str | None]:
        """Run yt-dlp once and verify the result.

        Returns ``(ok, message, action)``; ``action`` is ``"overwrite"`` (existing file is damaged:
        re-download), ``"403"`` (the site refused a stream: worth retrying) or None.
        """
        args = build_download_args(url, self.options, self.deps, self.temp_dir, force_overwrites,
                                   exclude_formats=self._excluded, player_client=client)
        self.state = "downloading"
        self._emit(None, "downloading", "Starting...")
        try:
            proc = _popen(args)
        except OSError as exc:
            return False, f"Could not start yt-dlp: {exc}", None
        with self._lock:
            self._proc = proc
        if self._cancelled.is_set():
            _kill_tree(proc)

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line:
                self._handle_line(line)
        proc.wait()
        if self._cancelled.is_set():
            return False, "Cancelled.", None

        # Success is decided by the files, not by yt-dlp's exit code: a non-zero exit with a
        # complete, playable file means a non-fatal step (thumbnail, metadata...) failed.
        candidates = self._collect_outputs()
        final: list[str] = []
        labels: list[str] = []
        damaged_existing = damaged = False
        for i, (path, vid, strict) in enumerate(candidates):
            if self._cancelled.is_set():
                return False, "Cancelled.", None
            existing = any(_same_path(path, p) for p in self._existing)
            label = self._validate(path, vid, strict=strict or existing)
            if label is None:
                damaged = True
                if existing:
                    damaged_existing = True
                elif not strict and not _claimed_by_other(path, self.id):
                    _remove_file(path)  # never leave a broken file behind looking like a download
                continue
            if strict:  # rescued from the scratch folder: move it where yt-dlp would have
                moved = self._move_to_output(path, vid)
                if moved is None:
                    continue
                path, adopted = moved
                existing = existing or adopted
            if self.options.kind == "mp4" and not existing:
                self._ensure_mp4_compat(path, i, len(candidates))
                if self._cancelled.is_set():
                    return False, "Cancelled.", None
            path, adopted = self._relabel(path, label, vid)
            if existing or adopted:
                self._existing.add(path)
            final.append(path)
            labels.append(label)

        if not final:
            if damaged_existing and not force_overwrites:
                return False, "", "overwrite"
            has_error = any(ln.startswith("ERROR:") for ln in self._tail)
            if damaged and not has_error:
                self.error_kind = "error"
                return False, "The downloaded file is incomplete or damaged. Please try again.", None
            if self._saw_403:
                self.error_kind = "http_403"
                if not self.options.playlist:
                    _failing, partway = self._failing_format()
                    return False, http_403_message(_site_name(None, url),
                                                   "expired" if partway else "refused"), "403"
            kind, message = diagnose_error(list(self._tail), self.url, self.options.cookies_browser)
            self.error_kind = kind
            return False, message, None

        self.output_paths = final
        self.output_path = final[0] if len(final) == 1 else os.path.dirname(final[-1])
        self.delivered_quality = self._summarize_labels(labels)
        self.already_downloaded = all(any(_same_path(p, e) for e in self._existing) for p in final)
        if proc.returncode != 0 and any(ln.startswith("ERROR:") for ln in self._tail) \
                and not self.options.playlist:
            self.warning = friendly_error(list(self._tail), self.url, self.options.cookies_browser)
        fallback = self._fallback_note(labels)
        if fallback:
            self.warning = fallback
        if self.options.playlist:
            failed = max(self._expected_items - len(final), 0) if self._expected_items else self._failed_items
            if failed:
                return True, (f"Finished with errors: {failed} of {self._expected_items or '?'} item(s) "
                              f"failed. Saved to {self.output_path}"), None
        elif self.already_downloaded:
            return True, f"Already in your download folder ({self.delivered_quality})", None
        return True, self.output_path, None

    def _fallback_note(self, labels: list[str]) -> str | None:
        """Explain a lower resolution caused by refused (HTTP 403) streams."""
        if not self._excluded or self.options.kind != "mp4" or not labels:
            return None
        planned = max(self._planned.values(), default=0)
        m = re.match(r"^(\d+)p$", labels[0])
        got = int(m.group(1)) if m else 0
        if not planned or not got or got >= planned:
            return None
        site = _site_name(None, self.url)
        return (f"{_res_name(planned)} isn't downloadable for this video right now ({site} blocked "
                f"that stream with HTTP 403). Saved in {_res_name(got)} instead.")

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
            vid, sep, path = line[len(_FILE_TAG):].partition(_SEP)
            if not sep:
                vid, path = "", vid
            path = path.strip()
            if path:
                self._printed.append((vid, path))
                if vid:
                    self._video_ids.add(vid)
            return
        if line.startswith(_INFO_TAG):
            parts = line[len(_INFO_TAG):].split(_SEP)
            if len(parts) >= 3 and parts[0] not in ("", "NA"):
                try:
                    duration: float | None = float(parts[1])
                except ValueError:
                    duration = None
                self._items[parts[0]] = {"duration": duration, "vcodec": parts[2],
                                         "acodec": parts[5] if len(parts) > 5 else "NA"}
                self._video_ids.add(parts[0])
                dims = [int(x) for x in parts[3:5] if x.isdigit() and int(x) > 0]
                if dims and parts[0] not in self._planned:  # first attempt's choice
                    self._planned[parts[0]] = min(dims)
            return

        self._tail.append(line)
        if "HTTP Error 403" in line:
            self._saw_403 = True
        m = re.match(r'^WARNING: Cannot move file ".+" out of temporary directory since "(.+)" already exists',
                     line)
        if m:  # another job placed the same quality first: that file is ours too
            self._existing.add(m.group(1))
            return
        m = re.match(r"^\[download\] Destination: (.+)$", line)
        if m:
            self._touched.add(m.group(1).strip())
            if self.state != "downloading":
                self.state = "downloading"
            return
        m = re.match(r"^\[download\] (.+) has already been downloaded$", line)
        if m:
            self._existing.add(m.group(1).strip())
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
            self._fmt_list = m.group(2).split("+")
            self._fmt_done, self._fmt_bytes = set(), {}
            self._part_count = m.group(2).count("+") + 1
            self._part_index = 0
            self._part_frac = 0.0
            return
        m = re.match(r"^\[info\] Writing video thumbnail .* to: (.+)$", line)
        if m:
            self._touched.add(m.group(1).strip())
            return
        m = re.match(r'^\[(?:Merger\] Merging formats into|Metadata\] Adding metadata to'
                     r'|EmbedThumbnail\] ffmpeg: Adding thumbnail to) "(.+)"$', line)
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
        fid = parts[12] if len(parts) > 12 else ""

        def num(s: str) -> float | None:
            try:
                return float(s)
            except ValueError:
                return None

        d, t_exact, t_est = num(done), num(total), num(total_est)
        if fid and fid != "NA":
            if status == "finished":
                self._fmt_done.add(fid)
            elif d:
                self._fmt_bytes[fid] = d
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

    # ------------------------------------------------------------ result verification
    def _final_exts(self) -> tuple[str, ...]:
        return {"mp4": (".mp4", ".mkv", ".webm"), "mp3": (".mp3",), "m4a": (".m4a",)}[
            self.options.kind if self.options.kind in KINDS else "mp4"]

    def _collect_outputs(self) -> list[tuple[str, str, bool]]:
        """Final files of this run as ``(path, video id, strict)``.

        ``strict`` marks a file rescued from the scratch folder (yt-dlp failed after producing it):
        it is only accepted after a full check (streams and duration).
        """
        out: list[tuple[str, str, bool]] = []
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()

        def add(path: str, vid: str, strict: bool) -> None:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen_paths:
                seen_paths.add(key)
                out.append((path, vid, strict))
                if vid:
                    seen_ids.add(vid)

        for vid, path in self._printed:
            if os.path.isfile(path):
                add(path, vid, False)
                continue
            # Printed path doesn't match disk (encoding quirk / renamed meanwhile): look it up by
            # id *and* requested quality label, so a different quality is never reported.
            for p in self._search(self.options.out_dir, vid):
                add(p, vid, False)
        for vid in list(self._items):
            if vid in seen_ids or not self.temp_dir:
                continue
            for p in self._search(self.temp_dir, vid):
                add(p, vid, True)
        return out

    def _search(self, folder: str, vid: str) -> list[str]:
        if not vid or not folder or not os.path.isdir(folder):
            return []
        label = requested_label(self.options)
        pattern = os.path.join(glob.escape(folder), "**",
                               f"*[[]{glob.escape(vid)}[]] - {glob.escape(label)}.*")
        tmp = os.path.normcase(os.path.abspath(self.temp_dir)) if self.temp_dir else None
        found = []
        for p in glob.glob(pattern, recursive=True):
            if not p.lower().endswith(self._final_exts()) or _INTERMEDIATE_RE.search(p):
                continue
            if tmp and folder != self.temp_dir and os.path.normcase(os.path.abspath(p)).startswith(tmp):
                continue
            found.append(p)
        return sorted(found, key=os.path.getmtime)

    def _validate(self, path: str, vid: str, strict: bool) -> str | None:
        """Check a finished file; return its quality label, or None if it's missing/broken.

        Rules: non-empty; MP4 has a (non-cover) video stream (or audio, if the source itself is
        audio-only); MP3/M4A have an audio stream. ``strict`` also requires audio in MP4s and a
        duration of at least 90 % of the source's (catches truncated/half-converted files).
        """
        try:
            if os.path.getsize(path) <= 0:
                return None
        except OSError:
            return None
        req = requested_label(self.options)
        media = _probe_media(path, self.deps)
        if media is None:  # ffprobe unavailable: trust yt-dlp
            return req
        streams = media["streams"]
        video = [s for s in streams if s["type"] == "video" and not s["attached_pic"]]
        audio = [s for s in streams if s["type"] == "audio"]
        info = self._items.get(vid) or {}
        kind = self.options.kind if self.options.kind in KINDS else "mp4"
        if kind == "mp4":
            source_audio_only = info.get("vcodec") == "none"
            if not video and not (audio and source_audio_only):
                return None
            if strict and video and not audio and info.get("acodec") not in ("none", None):
                return None  # the source has sound, this file doesn't
        elif not audio:
            return None
        expected = info.get("duration")
        actual = media["duration"]
        if strict and expected and actual is not None and actual < expected * 0.9 - 2:
            return None

        if kind == "mp4":
            dims = [int(x) for x in (video[0]["width"], video[0]["height"]) if isinstance(x, int) and x > 0] \
                if video else []
            return f"{min(dims)}p" if dims else req
        if kind == "mp3":
            return req
        return _kbps_label(audio[0].get("bit_rate")) or "m4a"

    def _move_to_output(self, path: str, vid: str) -> tuple[str, bool] | None:
        """Move a rescued file from the scratch folder to the same relative place in out_dir."""
        assert self.temp_dir is not None
        rel = os.path.relpath(path, self.temp_dir)
        dest = os.path.join(self.options.out_dir, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except OSError:
            return None
        return self._place(path, dest, vid)

    def _place(self, src: str, dest: str, vid: str) -> tuple[str, bool] | None:
        """Move ``src`` to ``dest`` without ever replacing another job's (or a good existing) file.

        Identical quality means an identical file: if ``dest`` already exists and is valid, or
        another running job produced it, ``src`` is dropped and ``dest`` is reused. Returns
        ``(path, reused_existing)``, or None if the move failed.
        """
        key = os.path.normcase(os.path.abspath(dest))
        with _OUTPUT_LOCK:
            if os.path.isfile(dest) and not _same_path(src, dest):
                owner = _CLAIMS.get(key)
                if (owner is not None and owner != self.id) or \
                        self._validate(dest, vid, strict=True) is not None:
                    _remove_file(src)
                    return dest, True
            for _ in range(20):
                try:
                    os.replace(src, dest)
                    _CLAIMS[key] = self.id
                    return dest, False
                except PermissionError:  # target open in a player / scanned by antivirus
                    time.sleep(0.3)
                except OSError:
                    break
        return None

    def _relabel(self, path: str, label: str, vid: str = "") -> tuple[str, bool]:
        """Rename ``… - <requested>.<ext>`` to ``… - <delivered>.<ext>`` (e.g. best -> 2160p).

        Returns ``(final path, reused_existing)``.
        """
        req = requested_label(self.options)
        root, ext = os.path.splitext(path)
        tail = f" - {req}"
        if label != req and root.endswith(tail):
            placed = self._place(path, root[: -len(tail)] + f" - {label}{ext}", vid)
            if placed is not None:
                return placed
        _claim(path, self.id)
        return path, False

    @staticmethod
    def _summarize_labels(labels: list[str]) -> str | None:
        if not labels:
            return None
        if len(set(labels)) == 1:
            return labels[0]

        def rank(label: str) -> int:
            m = re.match(r"^(\d+)", label)
            return int(m.group(1)) if m else -1

        return max(labels, key=rank)

    # ------------------------------------------------------------ post-checks
    def _ensure_mp4_compat(self, path: str, index: int, total: int) -> None:
        """Make an MP4 play in Windows' built-in player.

        * audio that isn't AAC/MP3/ALAC (e.g. Opus) is re-encoded to AAC (video is copied);
        * HEVC/H.265 video (needs a paid codec extension on many PCs; common on TikTok) is
          re-encoded to H.264 at the same resolution.
        Resolution is never changed. The conversion is written to the job's scratch folder and
        only replaces the original after ffmpeg succeeded; failures leave the original untouched.
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
        if self.temp_dir and os.path.isdir(self.temp_dir):
            tmp = os.path.join(self.temp_dir, f"compat-{index}.mp4")
        else:
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
                for _ in range(10):
                    try:
                        os.replace(tmp, path)
                        break
                    except PermissionError:
                        time.sleep(0.3)
            elif not self._cancelled.is_set():
                self._tail.append("".join(err_chunks)[-300:])
        except OSError:
            pass
        finally:
            if os.path.exists(tmp):
                _remove_file(tmp)

    # ------------------------------------------------------------ scratch folder / cleanup
    def _make_temp_dir(self, out_dir: str) -> str | None:
        root = os.path.join(out_dir, TEMP_DIR_NAME)
        try:
            os.makedirs(root, exist_ok=True)
            _hide_dir(root)
            self._clean_stale_temp(root)
            path = os.path.join(root, self.id)
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            return None  # fall back to downloading straight into out_dir

    @staticmethod
    def _clean_stale_temp(root: str) -> None:
        """Remove scratch folders left behind by a crashed app (untouched for days)."""
        now = time.time()
        try:
            entries = list(os.scandir(root))
        except OSError:
            return
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                newest = entry.stat().st_mtime
                for dirpath, _dirs, files in os.walk(entry.path):
                    for f in files:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, f)))
                if now - newest > _STALE_TEMP_SECONDS:
                    shutil.rmtree(entry.path, ignore_errors=True)
            except OSError:
                continue

    def _cleanup_partials(self) -> None:
        """Remove this job's scratch folder and any partial files it left next to the output."""
        if self.temp_dir:
            _remove_tree(self.temp_dir)
            try:
                os.rmdir(os.path.dirname(self.temp_dir))  # only succeeds when no other job uses it
            except OSError:
                pass
        tmp = os.path.normcase(os.path.abspath(self.temp_dir)) + os.sep if self.temp_dir else None
        final = {os.path.normcase(os.path.abspath(p)) for p in self.output_paths}
        candidates: set[str] = set()
        for path in self._touched:
            if tmp and os.path.normcase(os.path.abspath(path)).startswith(tmp):
                continue  # already gone with the scratch folder
            root, ext = os.path.splitext(path)
            candidates.update({path + ".part", path + ".ytdl", f"{root}.temp{ext}"})
            candidates.update(glob.glob(glob.escape(path) + ".part*"))
            if _INTERMEDIATE_RE.search(path):
                candidates.add(path)
            if not self.temp_dir and self._cancelled.is_set() and \
                    ext.lower() in (".webp", ".jpg", ".jpeg", ".png"):
                candidates.update(f"{root}{e}" for e in (".webp", ".jpg", ".jpeg", ".png"))
        for p in candidates:
            if os.path.normcase(os.path.abspath(p)) not in final:
                _remove_file(p)


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
