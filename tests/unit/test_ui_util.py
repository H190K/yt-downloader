"""ui.util pure helpers (no Tk window is created)."""
from __future__ import annotations

import pytest

from ui import util


# ---------------------------------------------------------------- quality_options
def test_quality_options_none_gives_standard_ladder():
    assert util.quality_options(None) == ["best", "2160", "1440", "1080", "720", "480", "360"]


def test_quality_options_real_heights_sorted_deduped():
    assert util.quality_options([720, 1080, 360, 1080, 0, -5]) == ["best", "1080", "720", "360"]


def test_quality_options_keeps_odd_heights():
    # Shorts are 1080 (smaller side); Instagram portrait can be 1350 etc. Passed through exactly.
    assert util.quality_options([1350, 1080, 720]) == ["best", "1350", "1080", "720"]


def test_quality_options_empty_list_is_best_only():
    assert util.quality_options([]) == ["best"]


# ---------------------------------------------------------------- quality_label
@pytest.mark.parametrize("value,label", [
    ("best", "Best available"),
    ("2160", "4K (2160p)"),
    ("1440", "2K (1440p)"),
    ("1080", "1080p Full HD"),
    ("720", "720p HD"),
    ("480", "480p"),
    ("360", "360p"),
    ("4320", "8K (4320p)"),
    ("1350", "1350p"),
    ("144", "144p"),
])
def test_quality_label(value, label):
    assert util.quality_label(value) == label


def test_quality_labels_are_unique_for_menu_mapping():
    # download_page builds {label: value}; duplicate labels would silently drop a choice.
    values = util.quality_options([4320, 2160, 1440, 1350, 1080, 720, 480, 360, 240, 144])
    labels = [util.quality_label(v) for v in values]
    assert len(labels) == len(set(labels))


# ---------------------------------------------------------------- preferred_quality
@pytest.mark.parametrize("pref,values,expected", [
    ("1080", ["best", "2160", "1080", "720"], "1080"),
    ("1080", ["best", "2160", "1440", "720", "360"], "720"),   # highest offered below
    ("2160", ["best", "1080", "720"], "1080"),
    ("360", ["best", "1080", "720"], "best"),                  # nothing at/below -> best
    ("best", ["best", "1080"], "best"),
    ("garbage", ["best", "1080"], "best"),
    ("", ["best", "1080"], "best"),
    ("1080", ["best"], "best"),
])
def test_preferred_quality(pref, values, expected):
    assert util.preferred_quality(pref, values) == expected


# ---------------------------------------------------------------- URL helpers
@pytest.mark.parametrize("text,ok", [
    ("https://youtu.be/5Y3a06rJjoU", True),
    ("youtu.be/5Y3a06rJjoU", True),
    ("https://www.youtube.com/shorts/abcdEFGhijk", True),
    ("hello world", False),
    ("", False),
    ("   ", False),
])
def test_looks_like_url(text, ok):
    assert util.looks_like_url(text) is ok


@pytest.mark.parametrize("url,name", [
    ("https://youtu.be/5Y3a06rJjoU", "YouTube"),
    ("https://www.youtube.com/shorts/abc", "YouTube"),
    ("https://music.youtube.com/watch?v=abc", "YouTube Music"),
    ("https://vm.tiktok.com/abc", "TikTok"),
    ("https://vimeo.com/76979871", "Vimeo"),
    ("https://x.com/a/status/1", "X (Twitter)"),
    ("not a url", None),
])
def test_detect_platform(url, name):
    assert util.detect_platform(url) == name


def test_ui_normalize_url_adds_scheme():
    assert util.normalize_url(" youtu.be/x ") == "https://youtu.be/x"
    assert util.normalize_url("http://a.com") == "http://a.com"


def test_ui_friendly_error_single_line():
    assert util.friendly_error(RuntimeError("ERROR: boom\ntraceback...")) == "boom"
    assert util.friendly_error(ValueError()) == "ValueError"


@pytest.mark.parametrize("sec,s", [(None, None), (0, None), (59, "0:59"), (61, "1:01"), (3600, "1:00:00")])
def test_fmt_duration(sec, s):
    assert util.fmt_duration(sec) == s


def test_truncate():
    assert util.truncate("a  b\n c", 10) == "a b c"
    out = util.truncate("x" * 50, 10)
    assert len(out) == 10 and out.endswith("…")
