"""Design tokens (colors, fonts, icons) and small widget factories.

Every color is a ``(light, dark)`` tuple so customtkinter re-colors widgets automatically when
the appearance mode changes; no manual recoloring is needed anywhere in the UI.
"""
from __future__ import annotations

import os
import tkinter.font as tkfont
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

Color = tuple[str, str]

# --------------------------------------------------------------------------- palette
BG: Color = ("#F3F4F8", "#0E1016")
SIDEBAR: Color = ("#FFFFFF", "#14171F")
SIDEBAR_BORDER: Color = ("#E6E8EF", "#1E2230")
CARD: Color = ("#FFFFFF", "#181C27")
CARD_BORDER: Color = ("#E3E6EE", "#252A39")
FIELD: Color = ("#F2F4F8", "#11141C")
FIELD_BORDER: Color = ("#D8DCE6", "#2C3244")
CONTROL: Color = ("#ECEEF4", "#232838")
CONTROL_HOVER: Color = ("#E1E4EC", "#2C3246")
HOVER: Color = ("#F0F1F6", "#1F2433")

TEXT: Color = ("#12141C", "#E9EBF3")
TEXT_2: Color = ("#5D6376", "#9EA4B8")
TEXT_3: Color = ("#8A90A2", "#687089")

ACCENT: Color = ("#5046E5", "#6C63F5")
ACCENT_HOVER: Color = ("#4338CA", "#5A51E6")
ACCENT_SOFT: Color = ("#EEEDFE", "#24234A")
ACCENT_TEXT: Color = ("#4338CA", "#A7A2FF")
ON_ACCENT: Color = ("#FFFFFF", "#FFFFFF")

SUCCESS: Color = ("#15803D", "#34D399")
SUCCESS_SOFT: Color = ("#DCFCE7", "#11342A")
WARNING: Color = ("#B45309", "#FBBF24")
WARNING_SOFT: Color = ("#FEF3C7", "#382C0F")
DANGER: Color = ("#DC2626", "#F87171")
DANGER_SOFT: Color = ("#FEE2E2", "#3A1619")
DANGER_HOVER: Color = ("#FCD9D9", "#4A1C20")

TRACK: Color = ("#E4E7EF", "#262B3B")
SCROLL: Color = ("#D3D7E1", "#2A3042")
SCROLL_HOVER: Color = ("#BCC2CF", "#39405A")

# --------------------------------------------------------------------------- fonts
FAMILY = "Segoe UI"
FAMILY_SEMIBOLD = "Segoe UI Semibold"


@lru_cache(maxsize=None)
def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """Return a cached CTkFont. ``weight`` is "normal", "semibold" or "bold".

    Must be called after the root window exists.
    """
    if weight == "semibold":
        return ctk.CTkFont(family=FAMILY_SEMIBOLD, size=size)
    return ctk.CTkFont(family=FAMILY, size=size, weight="bold" if weight == "bold" else "normal")


# --------------------------------------------------------------------------- icons
class Icon:
    """Glyph code points of Segoe Fluent Icons / Segoe MDL2 Assets."""

    DOWNLOAD = "\uE896"
    QUEUE = "\uE8FD"
    SETTINGS = "\uE713"
    INFO = "\uE946"
    SYNC = "\uE895"
    PASTE = "\uE77F"
    SEARCH = "\uE721"
    FOLDER = "\uE838"
    CLOSE = "\uE711"
    VIDEO = "\uE714"
    MUSIC = "\uE8D6"
    CHECK = "\uE73E"
    ERROR = "\uE783"
    WARNING = "\uE7BA"
    LINK = "\uE71B"
    REFRESH = "\uE72C"
    DELETE = "\uE74D"
    OPEN = "\uE8A7"
    PLAYLIST = "\uE90B"
    CLOCK = "\uE823"
    GLOBE = "\uE774"
    PERSON = "\uE77B"
    LOCK = "\uE72E"
    PALETTE = "\uE790"
    TOOLS = "\uE90F"
    CODE = "\uE943"
    BUG = "\uEBE8"
    HEART = "\uEB51"
    FILE = "\uE8A5"
    SHOW = "\uE7B3"


_ICON_FONT_FILES = ("SegoeIcons.ttf", "segmdl2.ttf")


@lru_cache(maxsize=1)
def icon_family() -> str:
    """Name of the best available icon font family (needs a Tk root)."""
    families = set(tkfont.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in families:
            return name
    return FAMILY


@lru_cache(maxsize=None)
def icon_font(size: int = 16) -> ctk.CTkFont:
    return ctk.CTkFont(family=icon_family(), size=size)


@lru_cache(maxsize=1)
def _icon_font_file() -> str | None:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in _ICON_FONT_FILES:
        path = Path(windir) / "Fonts" / name
        if path.is_file():
            return str(path)
    return None


def _render_glyph(glyph: str, px: int, color: str) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    path = _icon_font_file()
    if path is None:
        return img
    fnt = ImageFont.truetype(path, px)
    ImageDraw.Draw(img).text((px / 2, px / 2), glyph, font=fnt, fill=color, anchor="mm")
    return img


@lru_cache(maxsize=None)
def icon_image(glyph: str, size: int = 16, color: Color = TEXT) -> ctk.CTkImage | None:
    """Render an icon glyph to a theme-aware CTkImage (for use inside CTkButtons)."""
    if _icon_font_file() is None:
        return None
    px = size * 3
    return ctk.CTkImage(light_image=_render_glyph(glyph, px, color[0]),
                        dark_image=_render_glyph(glyph, px, color[1]), size=(size, size))


def load_app_logo(size: int) -> ctk.CTkImage | None:
    """Return icon.ico (largest frame) as a CTkImage, or None if unavailable."""
    from ui.backend import resource_path

    try:
        img = _largest_ico_frame(str(resource_path("assets/icon.ico")))
    except Exception:  # noqa: BLE001 - a missing/corrupt icon must never break the UI
        return None
    img = img.resize((size * 3, size * 3), Image.Resampling.LANCZOS)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


@lru_cache(maxsize=2)
def _largest_ico_frame(path: str) -> Image.Image:
    with Image.open(path) as ico:
        sizes = sorted(getattr(ico, "info", {}).get("sizes", set()) or [ico.size])
        try:
            ico.size = sizes[-1]  # type: ignore[misc]  # ICO plugin: select the frame
        except Exception:  # noqa: BLE001
            pass
        ico.load()
        return ico.convert("RGBA").copy()


# --------------------------------------------------------------------------- factories
def accent_button(master: Any, text: str, command: Callable[[], Any] | None = None, *,
                  icon: str | None = None, height: int = 38, width: int = 0,
                  size: int = 13, **kw: Any) -> ctk.CTkButton:
    """Primary (filled accent) button."""
    return ctk.CTkButton(
        master, text=text, command=command, height=height, width=width, corner_radius=8,
        fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
        text_color_disabled=("#C9C6F7", "#8C88C9"), font=font(size, "semibold"),
        image=icon_image(icon, 16, ON_ACCENT) if icon else None, compound="left", **kw)


def ghost_button(master: Any, text: str, command: Callable[[], Any] | None = None, *,
                 icon: str | None = None, height: int = 34, width: int = 0,
                 size: int = 12, danger: bool = False, **kw: Any) -> ctk.CTkButton:
    """Secondary (neutral outlined) button."""
    color = DANGER if danger else TEXT
    return ctk.CTkButton(
        master, text=text, command=command, height=height, width=width, corner_radius=8,
        fg_color=CONTROL, hover_color=DANGER_HOVER if danger else CONTROL_HOVER,
        border_width=0, text_color=color, text_color_disabled=TEXT_3,
        font=font(size, "semibold"),
        image=icon_image(icon, 14, color) if icon else None, compound="left", **kw)


def icon_button(master: Any, glyph: str, command: Callable[[], Any] | None = None, *,
                size: int = 30, color: Color = TEXT_2, danger: bool = False,
                **kw: Any) -> ctk.CTkButton:
    """Small square icon-only button."""
    return ctk.CTkButton(
        master, text="", width=size, height=size, corner_radius=8, command=command,
        fg_color="transparent", hover_color=DANGER_HOVER if danger else CONTROL_HOVER,
        image=icon_image(glyph, 14, DANGER if danger else color), **kw)


def option_menu(master: Any, values: list[str], command: Callable[[str], Any] | None = None,
                width: int = 200, **kw: Any) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        master, values=values, command=command, width=width, height=36, corner_radius=8,
        fg_color=CONTROL, button_color=CONTROL, button_hover_color=CONTROL_HOVER,
        text_color=TEXT, text_color_disabled=TEXT_3, font=font(13),
        dropdown_fg_color=CARD, dropdown_hover_color=ACCENT_SOFT, dropdown_text_color=TEXT,
        dropdown_font=font(13), dynamic_resizing=False, **kw)


def segmented(master: Any, values: list[str], command: Callable[[str], Any] | None = None,
              **kw: Any) -> ctk.CTkSegmentedButton:
    """Pill-style segmented control (selected segment is a raised light surface)."""
    return ctk.CTkSegmentedButton(
        master, values=values, command=command, height=34, corner_radius=8, border_width=3,
        fg_color=CONTROL, selected_color=("#FFFFFF", "#3A4060"),
        selected_hover_color=("#FFFFFF", "#434A6E"), unselected_color=CONTROL,
        unselected_hover_color=CONTROL_HOVER, text_color=TEXT, text_color_disabled=TEXT_3,
        font=font(12, "semibold"), **kw)


def switch(master: Any, text: str = "", command: Callable[[], Any] | None = None,
           **kw: Any) -> ctk.CTkSwitch:
    return ctk.CTkSwitch(
        master, text=text, command=command, font=font(13), text_color=TEXT,
        progress_color=ACCENT, button_color=("#FFFFFF", "#E9EBF3"),
        button_hover_color=("#F4F4F8", "#FFFFFF"), fg_color=SCROLL, switch_width=40,
        switch_height=20, **kw)


def progress_bar(master: Any, **kw: Any) -> ctk.CTkProgressBar:
    return ctk.CTkProgressBar(master, height=6, corner_radius=3, fg_color=TRACK,
                              progress_color=ACCENT, **kw)


def label(master: Any, text: str = "", size: int = 13, weight: str = "normal",
          color: Color = TEXT, **kw: Any) -> ctk.CTkLabel:
    kw.setdefault("anchor", "w")
    kw.setdefault("justify", "left")
    kw.setdefault("height", 0)  # natural text height instead of CTk's fixed 28px
    return ctk.CTkLabel(master, text=text, font=font(size, weight), text_color=color, **kw)


def glyph(master: Any, char: str, size: int = 16, color: Color = TEXT_2, **kw: Any) -> ctk.CTkLabel:
    """Icon rendered as a font glyph inside a CTkLabel (recolors instantly with the theme)."""
    return ctk.CTkLabel(master, text=char, font=icon_font(size), text_color=color, **kw)
