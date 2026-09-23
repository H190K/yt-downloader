"""Pure helpers in core.engine: normalize_url, friendly_error, resolution detection, formatting."""
from __future__ import annotations

import pytest

from core.engine import (
    _DL_TAG,
    _FILE_TAG,
    _SEP,
    DownloadJob,
    JobOptions,
    _fmt_bytes,
    _fmt_eta,
    _heights_from,
    _res_of,
    _site_name,
    friendly_error,
    normalize_url,
)


# ---------------------------------------------------------------- normalize_url
@pytest.mark.parametrize("raw,expected", [
    ("youtu.be/5Y3a06rJjoU", "https://youtu.be/5Y3a06rJjoU"),
    ("https://youtu.be/5Y3a06rJjoU", "https://youtu.be/5Y3a06rJjoU"),
    ("  https://youtu.be/5Y3a06rJjoU?si=abc \n", "https://youtu.be/5Y3a06rJjoU?si=abc"),
    ('"https://youtu.be/5Y3a06rJjoU"', "https://youtu.be/5Y3a06rJjoU"),
    ("www.youtube.com/shorts/abcdEFGhijk", "https://www.youtube.com/shorts/abcdEFGhijk"),
    ("https://youtube.com/shorts/abcdEFGhijk?feature=share",
     "https://youtube.com/shorts/abcdEFGhijk?feature=share"),
    ("http://example.com/a", "http://example.com/a"),
    ("", ""),
    ("not a url", "not a url"),
])
def test_normalize_url_basic(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("https://vimeo.com/76979871", "https://player.vimeo.com/video/76979871"),
    ("https://www.vimeo.com/76979871/", "https://player.vimeo.com/video/76979871"),
    ("vimeo.com/76979871", "https://player.vimeo.com/video/76979871"),
    ("https://vimeo.com/76979871/abcdef1234", "https://player.vimeo.com/video/76979871?h=abcdef1234"),
    ("https://vimeo.com/76979871?share=copy", "https://player.vimeo.com/video/76979871"),
])
def test_normalize_url_vimeo_rewrite(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("url", [
    "https://vimeo.com/channels/staffpicks/76979871",
    "https://player.vimeo.com/video/76979871",
    "https://vimeo.com/showcase/123",
])
def test_normalize_url_vimeo_left_alone(url):
    assert normalize_url(url) == url


# ---------------------------------------------------------------- friendly_error
YT = "https://www.youtube.com/watch?v=x"


@pytest.mark.parametrize("stderr,needle", [
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser",
     "not a bot"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.",
     "age-restricted"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [instagram] x: Requested content is not available, rate-limit reached or login required",
     "requires login"),
    ("ERROR: [youtube] x: Join this channel to get access to members-only content", "members only"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country",
     "geo-blocked"),
    ("ERROR: [youtube] x: This video is not available in your country", "geo-blocked"),
    ("ERROR: [vimeo] x: This video is not available from your location due to geo restriction",
     "geo-blocked"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader",
     "unavailable"),
    ("ERROR: Unsupported URL: https://example.com/", "isn't supported"),
    ("ERROR: [generic] 'foo' is not a valid URL.", "valid link"),
    ("ERROR: [youtube] x: This video is DRM protected", "DRM"),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", "rate-limiting"),
    ("ERROR: [youtube] x: This live event will begin in 3 hours.", "hasn't started"),
    ("ERROR: [youtube] x: Requested format is not available. Use --list-formats",
     "quality/format isn't available"),
    ("ERROR: [youtube] x: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>",
     "Network error"),
    ("ERROR: Postprocessing: ffmpeg not found. Please install or provide the path", "FFmpeg is missing"),
    ("ERROR: unable to write data: [Errno 28] No space left on device", "disk space"),
    ("ERROR: unable to open for writing: [Errno 13] Permission denied: 'C:\\x.part'", "permission denied"),
    ("ERROR: Failed to decrypt with DPAPI", "Firefox"),
    ("ERROR: could not find chrome cookies database in \"C:\\...\"", "cookies were found"),
    ("ERROR: Could not copy Chrome cookie database. See https://github.com/...", "Close it"),
])
def test_friendly_error_mapping(stderr, needle):
    msg = friendly_error(["[youtube] x: Downloading webpage", stderr], YT)
    assert needle.lower() in msg.lower(), msg
    assert not msg.startswith("ERROR"), msg


def test_friendly_error_accepts_string_and_uses_last_error_line():
    text = "WARNING: something\nERROR: [youtube] x: first\nERROR: [youtube] abc123: Totally new failure"
    assert friendly_error(text, YT) == "Totally new failure"


def test_friendly_error_empty_output():
    assert friendly_error([], YT) == "Download failed for an unknown reason."
    assert friendly_error("", YT) == "Download failed for an unknown reason."


def test_friendly_error_truncates_to_300_chars():
    assert len(friendly_error("ERROR: " + "x" * 1000, YT)) <= 300


def test_friendly_error_cookie_hint_names_browser_and_site():
    msg = friendly_error("ERROR: [youtube] x: Private video", YT, cookies_browser="edge")
    assert "Edge" in msg and "YouTube" in msg
    msg = friendly_error("ERROR: [youtube] x: Private video", YT, cookies_browser=None)
    assert "Settings" in msg


@pytest.mark.parametrize("extractor,url,name", [
    ("youtube:tab", "", "YouTube"),
    (None, "https://youtu.be/x", "YouTube"),
    (None, "https://vm.tiktok.com/x", "TikTok"),
    (None, "https://x.com/a/status/1", "X/Twitter"),
    (None, "https://www.example.org/v", "example.org"),
    (None, "", "This site"),
])
def test_site_name(extractor, url, name):
    assert _site_name(extractor, url) == name


# ---------------------------------------------------------------- resolution labels
@pytest.mark.parametrize("fmt,res", [
    ({"width": 1920, "height": 1080}, 1080),
    ({"width": 1080, "height": 1920}, 1080),   # vertical Short -> 1080, not 1920
    ({"width": 3840, "height": 2160}, 2160),
    ({"height": 720}, 720),
    ({"width": None, "height": None}, 0),
    ({}, 0),
])
def test_res_of_is_smaller_side(fmt, res):
    assert _res_of(fmt) == res


def test_heights_from_formats():
    info = {"formats": [
        {"vcodec": "none", "acodec": "opus"},
        {"vcodec": "avc1", "width": 1080, "height": 1920},
        {"vcodec": "vp9", "width": 720, "height": 1280},
        {"vcodec": "vp9", "width": 720, "height": 1280},
        {"ext": "mhtml", "width": 48, "height": 27},
    ]}
    heights, has_video = _heights_from(info)
    assert heights == [1080, 720]
    assert has_video is True


def test_heights_from_audio_only():
    heights, has_video = _heights_from({"formats": [{"vcodec": "none", "acodec": "mp3"}]})
    assert heights == [] and has_video is False


# ---------------------------------------------------------------- formatting
@pytest.mark.parametrize("n,s", [(None, ""), (0, ""), (512, "512 B"), (2048, "2.0 KB"),
                                 (5 * 1048576, "5.0 MB"), (3 * 1073741824, "3.0 GB")])
def test_fmt_bytes(n, s):
    assert _fmt_bytes(n) == s


@pytest.mark.parametrize("sec,s", [(0, "0:00"), (65, "1:05"), (3661, "1:01:01")])
def test_fmt_eta(sec, s):
    assert _fmt_eta(sec) == s


# ---------------------------------------------------------------- output parsing (no process)
def _job(fake_deps, tmp_path, **kw):
    kw.setdefault("out_dir", str(tmp_path))
    return DownloadJob("https://youtu.be/x", "t", JobOptions(**kw), fake_deps)


def test_file_tag_line_records_output(fake_deps, tmp_path):
    """Feed back exactly what the --print after_move template would print."""
    from core.engine import build_download_args
    job = _job(fake_deps, tmp_path)
    args = build_download_args(job.url, job.options, fake_deps)
    tpl = next(v for i, v in enumerate(args[1:], 1)
               if args[i - 1] == "--print" and v.startswith("after_move:"))[len("after_move:"):]
    path = rf"{tmp_path}\Title [x] - best.mp4"
    line = tpl.replace("%(id)s", "x").replace("%(filepath)s", path)
    assert line.startswith(_FILE_TAG)
    job._handle_line(line)
    # recorded either directly or (newer engine) as (video id, path) pending verification/rename
    recorded = list(job.output_paths) + [pp for _vid, pp in getattr(job, "_printed", [])]
    assert path in recorded


def test_progress_line_emits_fraction(fake_deps, tmp_path):
    job = _job(fake_deps, tmp_path, kind="mp3")
    seen: list[dict] = []
    job._on_progress = lambda _j, p: seen.append(p)
    fields = ["downloading", "500", "1000", "NA", "1024", "5", "NA", "NA", "none", "opus", "NA", "NA"]
    job._handle_line(_DL_TAG + _SEP.join(fields))
    assert seen and seen[-1]["fraction"] == pytest.approx(0.5)
    assert seen[-1]["status"] == "downloading"
    assert "audio" in seen[-1]["message"]


def test_progress_never_goes_backwards(fake_deps, tmp_path):
    job = _job(fake_deps, tmp_path, kind="mp3")
    seen: list[float] = []
    job._on_progress = lambda _j, p: seen.append(p["fraction"])
    for done in ("600", "300"):
        job._handle_line(_DL_TAG + _SEP.join(
            ["downloading", done, "1000", "NA", "1", "1", "NA", "NA", "none", "aac", "NA", "NA"]))
    assert seen == sorted(seen)


def test_cancel_before_start(fake_deps, tmp_path):
    job = _job(fake_deps, tmp_path)
    job.cancel()
    assert job.state == "cancelled"
    assert job.wait(0) is True
