"""Pure helpers used by the UI: URL/platform detection, formatting, thumbnails, shell actions."""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from urllib.parse import urlparse

from PIL import Image, ImageDraw

# --------------------------------------------------------------------------- platforms
_PLATFORMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube"),
    (("music.youtube.com",), "YouTube Music"),
    (("instagram.com", "instagr.am"), "Instagram"),
    (("tiktok.com",), "TikTok"),
    (("facebook.com", "fb.watch", "fb.com"), "Facebook"),
    (("x.com", "twitter.com"), "X (Twitter)"),
    (("soundcloud.com",), "SoundCloud"),
    (("vimeo.com",), "Vimeo"),
    (("reddit.com", "redd.it"), "Reddit"),
    (("twitch.tv",), "Twitch"),
    (("dailymotion.com", "dai.ly"), "Dailymotion"),
    (("pinterest.com", "pin.it"), "Pinterest"),
    (("threads.net", "threads.com"), "Threads"),
    (("bilibili.com", "b23.tv"), "Bilibili"),
    (("bandcamp.com",), "Bandcamp"),
    (("snapchat.com",), "Snapchat"),
    (("linkedin.com",), "LinkedIn"),
    (("tumblr.com",), "Tumblr"),
    (("rumble.com",), "Rumble"),
    (("kick.com",), "Kick"),
)

_EXTRACTORS = {
    "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok", "facebook": "Facebook",
    "twitter": "X (Twitter)", "soundcloud": "SoundCloud", "vimeo": "Vimeo", "reddit": "Reddit",
    "twitch": "Twitch", "dailymotion": "Dailymotion", "pinterest": "Pinterest",
    "bilibili": "Bilibili", "bandcamp": "Bandcamp", "threads": "Threads",
}

_URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[a-z]{2,}(:\d+)?(/\S*)?$", re.IGNORECASE)


def looks_like_url(text: str) -> bool:
    text = text.strip()
    return bool(text) and " " not in text and bool(_URL_RE.match(text))


def normalize_url(text: str) -> str:
    text = text.strip()
    if text and not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        text = "https://" + text
    return text


def detect_platform(url: str) -> str | None:
    """Return a friendly platform name for a URL, e.g. "YouTube", or None if unknown."""
    if not looks_like_url(url):
        return None
    host = (urlparse(normalize_url(url)).hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "vm.", "vt."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    # Most specific (longest) matching domain wins, e.g. music.youtube.com over youtube.com.
    matches = [(len(d), name) for domains, name in _PLATFORMS for d in domains
               if host == d or host.endswith("." + d)]
    if matches:
        return max(matches)[1]
    return host or None


def pretty_extractor(extractor: str | None) -> str | None:
    if not extractor:
        return None
    base = extractor.split(":")[0].strip().lower()
    if base in ("generic", ""):
        return None
    return _EXTRACTORS.get(base, base.replace("_", " ").title())


# --------------------------------------------------------------------------- formatting
QUALITY_LABELS: dict[str, str] = {
    "best": "Best available",
    "2160": "4K (2160p)",
    "1440": "2K (1440p)",
    "1080": "1080p Full HD",
    "720": "720p HD",
    "480": "480p",
    "360": "360p",
}
STANDARD_HEIGHTS = (2160, 1440, 1080, 720, 480, 360)
BITRATE_LABELS: dict[str, str] = {"320": "320 kbps", "256": "256 kbps", "192": "192 kbps",
                                  "128": "128 kbps"}
BROWSERS: dict[str, str | None] = {"None": None, "Chrome": "chrome", "Edge": "edge",
                                   "Firefox": "firefox", "Brave": "brave", "Opera": "opera"}


def quality_label(value: str) -> str:
    """Human label for a quality value ("best" or a height string like "1080" / "1920")."""
    if value in QUALITY_LABELS:
        return QUALITY_LABELS[value]
    if value == "4320":
        return "8K (4320p)"
    return f"{value}p"


def quality_options(heights: list[int] | None) -> list[str]:
    """Quality values (contract strings) offered for the given available heights.

    Known heights are passed through exactly (e.g. 1920 for vertical Shorts, 1350 on Instagram)
    so the chosen label maps to the real height. ``None`` means "nothing fetched yet" -> offer
    the standard ladder.
    """
    if heights is None:
        return ["best", *[str(h) for h in STANDARD_HEIGHTS]]
    real = sorted({int(h) for h in heights if h and int(h) > 0}, reverse=True)
    return ["best", *[str(h) for h in real]]


def preferred_quality(preferred: str, values: list[str]) -> str:
    """Pick ``preferred`` if offered, else the highest offered height below it, else "best"."""
    if preferred in values:
        return preferred
    try:
        target = int(preferred)
    except (TypeError, ValueError):
        return "best"
    below = [int(v) for v in values if v.isdigit() and int(v) <= target]
    return str(max(below)) if below else "best"


def short_quality(value: str) -> str:
    """Compact quality text for chips: "best" -> "Best", "2160" -> "4K", "720" -> "720p"."""
    if not value or value == "best":
        return "Best"
    if value == "2160":
        return "4K"
    if value == "4320":
        return "8K"
    return f"{value}p" if value.isdigit() else value


def requested_quality_text(kind: str, quality: str, mp3_bitrate: str) -> str:
    """What the user asked for, e.g. "MP4 · 720p", "MP3 · 320 kbps", "M4A · Best"."""
    if kind == "mp4":
        return f"MP4 · {short_quality(quality)}"
    if kind == "mp3":
        return f"MP3 · {BITRATE_LABELS.get(mp3_bitrate, f'{mp3_bitrate} kbps')}"
    return f"{kind.upper()} · Best"


def delivered_quality_text(delivered: str) -> str:
    """Engine's ``delivered_quality`` ("720p", "320kbps", "2160p") -> chip text ("720p", "4K").

    Returns "" when the value isn't a quality (the engine may fall back to e.g. "m4a").
    """
    d = delivered.strip()
    m = re.fullmatch(r"(\d+)\s*p", d, re.IGNORECASE)
    if m:
        return short_quality(m.group(1))
    m = re.fullmatch(r"(\d+)\s*kbps", d, re.IGNORECASE)
    if m:
        return f"{m.group(1)} kbps"
    return "" if d.lower() in ("", "mp4", "mp3", "m4a", "audio", "video") else d


def quality_shortfall(kind: str, requested: str, delivered: str | None) -> bool:
    """True when a specific video height was requested but a different one was saved.

    Only MP4 with an explicit height can fall short (the source had no such resolution);
    "best" and audio formats never do.
    """
    if kind != "mp4" or not delivered or not requested.isdigit():
        return False
    m = re.match(r"\s*(\d+)", delivered)
    return bool(m) and int(m.group(1)) != int(requested)


_YT_ID_RE = re.compile(r"^[\w-]{11}$")


def media_key(url: str) -> str:
    """Canonical identity of a link for duplicate detection.

    YouTube watch / youtu.be / shorts / embed links of the same video map to one key; other
    URLs are compared case-insensitively on host with the fragment and trailing slash dropped.
    """
    parsed = urlparse(normalize_url(url))
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    vid = ""
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com") or host == "youtube-nocookie.com":
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] in ("shorts", "embed", "live", "v") and len(parts) > 1:
            vid = parts[1]
        else:
            query = dict(q.split("=", 1) for q in parsed.query.split("&") if "=" in q)
            vid = query.get("v", "")
            if not vid and query.get("list"):
                return f"youtube:list:{query['list']}"
    if vid and _YT_ID_RE.match(vid):
        return f"youtube:{vid}"
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}{'?' + parsed.query if parsed.query else ''}"


def fmt_duration(seconds: float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_ago(epoch: float) -> str:
    if not epoch:
        return "never"
    delta = max(0, time.time() - epoch)
    if delta < 60:
        return "just now"
    if delta < 3600:
        n = int(delta // 60)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if delta < 86400:
        n = int(delta // 3600)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    n = int(delta // 86400)
    return f"{n} day{'s' if n != 1 else ''} ago"


def truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def middle_ellipsis(text: str, measure: Callable[[str], int], max_px: float) -> str:
    """Shorten ``text`` in the middle so ``measure(result) <= max_px``.

    Keeps the end visible, which matters for file names like "Title [id] - 720p.mp4".
    """
    if max_px <= 0 or measure(text) <= max_px:
        return text

    def cut(keep: int) -> str:
        tail = max(1, (keep * 3) // 5)
        head = max(0, keep - tail)
        return text[:head].rstrip() + "…" + text[len(text) - tail:].lstrip()

    lo, hi = 1, len(text) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(cut(mid)) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return cut(lo)


def friendly_error(exc: BaseException) -> str:
    """One-line, user-facing message for any exception (no tracebacks)."""
    msg = str(exc).strip() or exc.__class__.__name__
    first = msg.splitlines()[0].strip()
    first = re.sub(r"^(ERROR|error):\s*", "", first)
    return truncate(first, 300)


# --------------------------------------------------------------------------- thumbnails
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def fetch_thumbnail(url: str, size: tuple[int, int], radius: int = 10) -> Image.Image:
    """Download an image and return it cover-cropped to ``size`` with rounded corners (RGBA).

    Blocking; call from a worker thread.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 - http(s) thumbnail URL
        data = resp.read(15 * 1024 * 1024)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return round_corners(cover(img, size), radius)


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    nw, nh = max(tw, round(img.width * scale)), max(th, round(img.height * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    factor = 3  # supersampled mask -> smooth edges
    mask = Image.new("L", (img.width * factor, img.height * factor), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1),
                                           radius * factor, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask.resize(img.size, Image.Resampling.LANCZOS))
    return out


# --------------------------------------------------------------------------- shell
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        webbrowser.open(f"file://{path}")


def show_in_explorer(path: str) -> None:
    """Open Explorer with ``path`` selected (falls back to opening its folder)."""
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')  # noqa: S602,S603
    else:
        open_folder(os.path.dirname(path) or path)


def open_url(url: str) -> None:
    webbrowser.open(url)
