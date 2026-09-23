"""Settings page. Every change is persisted immediately through ``App.set_config``."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ui import theme as T
from ui.util import (BITRATE_LABELS, BROWSERS, QUALITY_LABELS, friendly_error, open_folder,
                     truncate)
from ui.widgets import ScrollArea, Card, PageHeader

if TYPE_CHECKING:
    from ui.app import App

THEMES = {"Dark": "dark", "Light": "light", "System": "system"}
KINDS = {"MP4": "mp4", "MP3": "mp3", "M4A": "m4a"}


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        PageHeader(self, "Settings", "Preferences are saved automatically.").grid(
            row=0, column=0, sticky="ew", pady=(0, 18))

        body = ScrollArea(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        # ---------------------------------------------------------------- downloads
        card = self._section(body, 0, T.Icon.DOWNLOAD, "Downloads")
        folder = self._row(card, 1, "Download folder", "Where finished files are saved.")
        self.dir_label = ctk.CTkLabel(folder, text="", fg_color=T.FIELD, corner_radius=8,
                                      height=34, width=260, anchor="w", padx=10,
                                      text_color=T.TEXT, font=T.font(12))
        self.dir_label.grid(row=0, column=0)
        T.ghost_button(folder, "Browse", self.app.choose_download_dir, width=80).grid(
            row=0, column=1, padx=(8, 0))
        T.icon_button(folder, T.Icon.FOLDER, self._open_dir, size=34).grid(row=0, column=2,
                                                                           padx=(4, 0))

        fmt = self._row(card, 2, "Default format", "Pre-selected on the Download page.")
        self.kind_seg = T.segmented(fmt, list(KINDS), command=self._on_kind, width=210)
        self.kind_seg.grid(row=0, column=0)

        q = self._row(card, 3, "Preferred video quality",
                      "Used when available; otherwise the best quality is picked.")
        self.quality_menu = T.option_menu(q, list(QUALITY_LABELS.values()),
                                          command=self._on_quality, width=210)
        self.quality_menu.grid(row=0, column=0)

        br = self._row(card, 4, "MP3 bitrate", "Higher is better quality and bigger files.",
                       last=True)
        self.bitrate_menu = T.option_menu(br, list(BITRATE_LABELS.values()),
                                          command=self._on_bitrate, width=210)
        self.bitrate_menu.grid(row=0, column=0)

        # ---------------------------------------------------------------- appearance
        card = self._section(body, 1, T.Icon.PALETTE, "Appearance")
        th = self._row(card, 1, "Theme", "System follows your Windows setting.", last=True)
        self.theme_seg = T.segmented(th, list(THEMES), command=self._on_theme, width=210)
        self.theme_seg.grid(row=0, column=0)

        # ---------------------------------------------------------------- accounts
        card = self._section(body, 2, T.Icon.LOCK, "Sign-in & cookies")
        ck = self._row(card, 1, "Use cookies from browser",
                       "Instagram, private, members-only or age-restricted videos need you to "
                       "be signed in. Pick the browser where you are logged in to that site.",
                       last=True)
        self.cookies_menu = T.option_menu(ck, list(BROWSERS), command=self._on_cookies, width=210)
        self.cookies_menu.grid(row=0, column=0)

        # ---------------------------------------------------------------- updates
        card = self._section(body, 3, T.Icon.SYNC, "Updates")
        up = self._row(card, 1, "Check for tool updates on startup",
                       "At most once a day, silently in the background.", last=True)
        self.auto_switch = T.switch(up, "", command=self._on_auto)
        self.auto_switch.grid(row=0, column=0)

        self.refresh()

    # ------------------------------------------------------------------ builders
    def _section(self, parent: Any, index: int, glyph: str, title: str) -> Card:
        card = Card(parent)
        card.grid(row=index, column=0, sticky="ew", pady=(0, 14), padx=(0, 6))
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))
        T.glyph(head, glyph, 15, T.ACCENT_TEXT, width=20).grid(row=0, column=0, padx=(0, 8))
        T.label(head, title, 15, "semibold").grid(row=0, column=1)
        return card

    def _row(self, card: Card, index: int, title: str, desc: str,
             last: bool = False) -> ctk.CTkFrame:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=index * 2, column=0, sticky="ew", padx=20, pady=(12, 18 if last else 12))
        row.grid_columnconfigure(0, weight=1)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.grid(row=0, column=0, sticky="w")
        T.label(text, title, 13, "semibold").grid(row=0, column=0, sticky="w")
        d = T.label(text, desc, 12, color=T.TEXT_2, wraplength=380)
        d.grid(row=1, column=0, sticky="w", pady=(2, 0))
        control = ctk.CTkFrame(row, fg_color="transparent")
        control.grid(row=0, column=1, sticky="e", padx=(16, 0))
        if index > 1:
            sep = ctk.CTkFrame(card, height=1, corner_radius=0, fg_color=T.CARD_BORDER)
            sep.grid(row=index * 2 - 1, column=0, sticky="ew", padx=20)
        return control

    # ------------------------------------------------------------------ sync
    def refresh(self) -> None:
        cfg = self.app.cfg
        self.dir_label.configure(text=truncate(str(cfg.get("download_dir", "")), 40))
        self.kind_seg.set({v: k for k, v in KINDS.items()}.get(cfg.get("default_kind"), "MP4"))
        self.quality_menu.set(QUALITY_LABELS.get(str(cfg.get("default_quality")), "Best available"))
        self.bitrate_menu.set(BITRATE_LABELS.get(str(cfg.get("mp3_bitrate")), "320 kbps"))
        self.theme_seg.set({v: k for k, v in THEMES.items()}.get(cfg.get("theme"), "Dark"))
        self.cookies_menu.set({v: k for k, v in BROWSERS.items()}.get(cfg.get("cookies_browser"),
                                                                     "None"))
        if cfg.get("auto_check_updates", True):
            self.auto_switch.select()
        else:
            self.auto_switch.deselect()

    def _open_dir(self) -> None:
        try:
            open_folder(str(self.app.cfg.get("download_dir")))
        except OSError as exc:
            self.app.toast(f"Couldn't open the folder: {friendly_error(exc)}", "error")

    def _on_kind(self, value: str) -> None:
        self.app.set_config(default_kind=KINDS[value])

    def _on_quality(self, value: str) -> None:
        inv = {v: k for k, v in QUALITY_LABELS.items()}
        self.app.set_config(default_quality=inv.get(value, "best"))

    def _on_bitrate(self, value: str) -> None:
        inv = {v: k for k, v in BITRATE_LABELS.items()}
        self.app.set_config(mp3_bitrate=inv.get(value, "320"))

    def _on_theme(self, value: str) -> None:
        self.app.set_config(theme=THEMES[value])
        self.app.apply_theme()

    def _on_cookies(self, value: str) -> None:
        browser = BROWSERS.get(value)
        self.app.set_config(cookies_browser=browser)
        if browser in ("chrome", "edge", "brave", "opera"):
            self.app.toast(f"Using {value} cookies. If reading them fails, fully close {value} "
                           "and try again.", "info")

    def _on_auto(self) -> None:
        self.app.set_config(auto_check_updates=bool(self.auto_switch.get()))
