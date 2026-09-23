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
    for domains, name in sorted(_PLATFORMS, key=lambda p: -max(len(d) for d in p[0])):
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
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
