"""build_download_args(): the yt-dlp command line for every kind/quality combination."""
from __future__ import annotations

import re

import pytest

from core import engine
from core.engine import JobOptions, build_download_args
from tests.unit._argv import main_output_template, value_of, values_of

URL = "https://www.youtube.com/watch?v=5Y3a06rJjoU"


def build(fake_deps, tmp_path, **kw):
    kw.setdefault("out_dir", str(tmp_path / "out"))
    return build_download_args(URL, JobOptions(**kw), fake_deps)


def sort_keys(args: list[str]) -> list[str]:
    s = value_of(args, "-S")
    assert s, f"no -S in {args}"
    return s.split(",")


# ---------------------------------------------------------------- common flags
def test_common_flags_present(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path)
    assert args[0] == str(fake_deps.ytdlp_path)
    for flag in ("--ignore-config", "--no-mtime", "--windows-filenames", "--newline"):
        assert flag in args, flag
    assert value_of(args, "--encoding") == "utf-8"
    assert value_of(args, "--ffmpeg-location") == str(fake_deps.ffmpeg_dir)
    assert value_of(args, "--js-runtimes") == f"deno:{fake_deps.deno_path}"
    assert any(v.startswith("after_move:") and "%(filepath)s" in v for v in values_of(args, "--print"))
    assert args[-2:] == ["--", URL], "URL must come after '--' so it can't be parsed as an option"


def test_no_js_runtime_when_deno_missing(fake_deps, tmp_path):
    fake_deps.deno_path.unlink()
    assert "--js-runtimes" not in build(fake_deps, tmp_path)


def test_cookies_browser_passed(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, cookies_browser="firefox")
    assert value_of(args, "--cookies-from-browser") == "firefox"
    assert "--cookies-from-browser" not in build(fake_deps, tmp_path)


def test_output_dir_is_home_path(fake_deps, tmp_path):
    out = str(tmp_path / "out")
    p_values = values_of(build(fake_deps, tmp_path, out_dir=out), "-P")
    # Either a plain -P <dir> or -P home:<dir> (when a separate temp: path is also given).
    assert out in p_values or f"home:{out}" in p_values, p_values


def test_single_video_template(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path)
    assert "--no-playlist" in args and "--yes-playlist" not in args
    tpl = main_output_template(args)
    assert "%(title)" in tpl and "[%(id)s]" in tpl and tpl.endswith(".%(ext)s")
    assert "/" not in tpl, "single videos must not go into a sub-folder"


def test_playlist_template(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, playlist=True)
    assert "--yes-playlist" in args and "--no-playlist" not in args
    assert "--ignore-errors" in args, "one broken entry must not abort the whole playlist"
    tpl = main_output_template(args)
    folder, _, name = tpl.partition("/")
    assert "playlist_title" in folder
    assert "playlist_index" in name and "[%(id)s]" in name and name.endswith(".%(ext)s")


# ---------------------------------------------------------------- MP4
def test_mp4_best(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, kind="mp4", quality="best")
    assert value_of(args, "-f") == "bv*+ba/b"
    keys = sort_keys(args)
    assert keys[0] == "res", "best = highest resolution first, no cap"
    assert value_of(args, "--merge-output-format") == "mp4"
    assert "-x" not in args


@pytest.mark.parametrize("q", ["2160", "1440", "1080", "720", "480", "360"])
def test_mp4_standard_heights(fake_deps, tmp_path, q):
    args = build(fake_deps, tmp_path, kind="mp4", quality=q)
    assert value_of(args, "-f") == "bv*+ba/b", "always merge best video + audio (never 360p progressive)"
    keys = sort_keys(args)
    assert keys[0] == f"res:{q}", "resolution must be the first sort key"
    # codec preference only breaks ties: it must come after the resolution key
    assert any(k.startswith("vcodec:h264") for k in keys)
    assert keys.index(next(k for k in keys if k.startswith("vcodec"))) > 0
    assert value_of(args, "--merge-output-format") == "mp4"


@pytest.mark.parametrize("q,expected", [
    ("1080", "res:1080"),    # a vertical Short: 1080x1920 is labelled 1080 (smaller side)
    ("1920", "res:1920"),    # a raw height passed through from quality_options()
    ("1350", "res:1350"),    # Instagram-style odd height
    ("720p", "res:720"),     # tolerate a trailing "p"
    ("  480 ", "res:480"),
])
def test_mp4_odd_heights(fake_deps, tmp_path, q, expected):
    assert sort_keys(build(fake_deps, tmp_path, kind="mp4", quality=q))[0] == expected


@pytest.mark.parametrize("q", ["", "abc", "0", "-720", None])
def test_mp4_invalid_quality_falls_back_to_best(fake_deps, tmp_path, q):
    assert sort_keys(build(fake_deps, tmp_path, kind="mp4", quality=q))[0] == "res"


def test_mp4_embed_flags(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, kind="mp4")
    assert "--embed-thumbnail" in args and "--embed-metadata" in args
    args = build(fake_deps, tmp_path, kind="mp4", embed_thumbnail=False, embed_metadata=False)
    assert "--embed-thumbnail" not in args and "--embed-metadata" not in args


def test_unknown_kind_is_mp4(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, kind="flac")
    assert value_of(args, "--merge-output-format") == "mp4"


# ---------------------------------------------------------------- MP3
@pytest.mark.parametrize("br", ["320", "256", "192", "128"])
def test_mp3_bitrates(fake_deps, tmp_path, br):
    args = build(fake_deps, tmp_path, kind="mp3", mp3_bitrate=br)
    assert value_of(args, "-f") == "ba/b"
    assert "-x" in args
    assert value_of(args, "--audio-format") == "mp3"
    assert value_of(args, "--audio-quality") == f"{br}K"
    assert "--merge-output-format" not in args


@pytest.mark.parametrize("br", ["999", "", "64", None])
def test_mp3_invalid_bitrate_defaults_to_320(fake_deps, tmp_path, br):
    args = build(fake_deps, tmp_path, kind="mp3", mp3_bitrate=br)
    assert value_of(args, "--audio-quality") == "320K"


# ---------------------------------------------------------------- M4A
def test_m4a(fake_deps, tmp_path):
    args = build(fake_deps, tmp_path, kind="m4a")
    fmt = value_of(args, "-f")
    assert fmt.startswith("ba[acodec^=mp4a]"), "prefer native AAC so it can be copied, not re-encoded"
    assert fmt.endswith("/b")
    assert "-x" in args
    assert value_of(args, "--audio-format") == "m4a"
    assert "--merge-output-format" not in args


# ---------------------------------------------------------------- quality suffix in file names
# Final names: "<title> [<id>] - 720p.mp4", "- 320kbps.mp3", "- <probed>kbps.m4a". The MP3 bitrate
# is known up front so it is in the -o template; MP4/M4A carry a provisional label in the template
# and are renamed to the *delivered* quality after the download (checked by the e2e tests).
_SUFFIX_LANDED = hasattr(engine, "requested_label") and hasattr(engine, "_kbps_label")
needs_suffix = pytest.mark.skipif(not _SUFFIX_LANDED, reason="engine quality-suffix change not present")


@needs_suffix
@pytest.mark.parametrize("br", ["320", "256", "192", "128"])
def test_mp3_template_has_bitrate_suffix(fake_deps, tmp_path, br):
    tpl = main_output_template(build(fake_deps, tmp_path, kind="mp3", mp3_bitrate=br))
    assert tpl.endswith(f"[%(id)s] - {br}kbps.%(ext)s"), tpl


@needs_suffix
@pytest.mark.parametrize("kind,kw", [("mp4", {"quality": "720"}), ("mp4", {"quality": "best"}),
                                     ("mp3", {}), ("m4a", {})])
def test_templates_have_suffix_single_and_playlist(fake_deps, tmp_path, kind, kw):
    for playlist in (False, True):
        tpl = main_output_template(build(fake_deps, tmp_path, kind=kind, playlist=playlist, **kw))
        assert re.search(r"\[%\(id\)s\] - [\w]+\.%\(ext\)s$", tpl), tpl


@needs_suffix
@pytest.mark.parametrize("kind,kw,label", [
    ("mp4", {"quality": "720"}, "720p"),
    ("mp4", {"quality": "1080p"}, "1080p"),
    ("mp4", {"quality": "best"}, "best"),
    ("mp4", {"quality": "junk"}, "best"),
    ("mp3", {"mp3_bitrate": "192"}, "192kbps"),
    ("mp3", {"mp3_bitrate": "999"}, "320kbps"),
])
def test_requested_label(kind, kw, label):
    assert engine.requested_label(JobOptions(kind=kind, **kw)) == label


def test_distinct_requests_get_distinct_provisional_names(fake_deps, tmp_path):
    """Concurrent jobs of one video into one folder must never share a file name while running."""
    if not _SUFFIX_LANDED:
        pytest.skip("engine quality-suffix change not present")
    tpls = [main_output_template(build(fake_deps, tmp_path, kind="mp4", quality=q))
            for q in ("best", "2160", "1440", "1080", "720", "480", "360")]
    tpls += [main_output_template(build(fake_deps, tmp_path, kind="mp3", mp3_bitrate=b))
             for b in ("320", "256", "192", "128")]
    tpls.append(main_output_template(build(fake_deps, tmp_path, kind="m4a")))
    assert len(set(tpls)) == len(tpls), tpls


@needs_suffix
@pytest.mark.parametrize("bps,label", [
    (129_500, "128kbps"),    # YouTube "128k" AAC probes slightly high
    (320_000, "320kbps"),
    (255_000, "256kbps"),
    (130_000, "128kbps"),
    (140_000, "140kbps"),    # not near a standard rate -> rounded
    (None, None), (0, None), (-5, None),
])
def test_kbps_label(bps, label):
    assert engine._kbps_label(bps) == label


def test_per_job_temp_dir(fake_deps, tmp_path):
    import inspect
    if "temp_dir" not in inspect.signature(build_download_args).parameters:
        pytest.skip("per-job temp dir not present")
    out, tmp = str(tmp_path / "out"), str(tmp_path / "out" / ".h190k-tmp" / "job1")
    args = build_download_args(URL, JobOptions(out_dir=out), fake_deps, temp_dir=tmp)
    p = values_of(args, "-P")
    assert f"home:{out}" in p and f"temp:{tmp}" in p, p
