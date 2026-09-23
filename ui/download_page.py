"""Download page: URL input, media preview and format/quality selection."""
from __future__ import annotations

import logging
import os
import threading
import tkinter
from typing import TYPE_CHECKING, Any

import customtkinter as ctk
from PIL import Image

from ui import theme as T
from ui.backend import EngineError, JobOptions, MediaInfo, fetch_info
from ui.queue_page import DuplicateJobError
from ui.util import (BITRATE_LABELS, detect_platform, fetch_thumbnail, fmt_duration,
                     friendly_error, looks_like_url, normalize_url, preferred_quality,
                     pretty_extractor, quality_label, quality_options, truncate)
from ui.widgets import Card, Chip, PageHeader, TileSelector

if TYPE_CHECKING:
    from ui.app import App

log = logging.getLogger(__name__)

THUMB_W, THUMB_H = 288, 162
FORMATS = (
    ("mp4", T.Icon.VIDEO, "MP4", "Video + audio"),
    ("mp3", T.Icon.MUSIC, "MP3", "Audio only"),
    ("m4a", T.Icon.MUSIC, "M4A", "Original audio"),
)


class DownloadPage(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.info: MediaInfo | None = None
        self._info_url = ""
        self._fetch_token = 0
        self._fetch_cancel: threading.Event | None = None
        self._fetching = False
        self._thumb_image: ctk.CTkImage | None = None
        self._quality_map: dict[str, str] = {}  # menu label -> value, for the current kind
        self._quality_kind = ""                  # kind that _quality_map was built for
        # Explicit user choices on this page. They win over the Settings defaults until the
        # page is reset (after queueing / F5), so a fetch finishing, switching format tiles or
        # changing Settings can never silently replace e.g. "720p" with "Best available".
        self._user_kind: str | None = None
        self._user_quality: dict[str, str] = {}  # kind -> value ("720", "320")
        self._wrap_job: str | None = None
        self._menu_values: list[str] = ["Best available"]
        self._menu_state = "normal"
        self._media_width = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = PageHeader(self, "Download",
                            "Paste a link from YouTube, Instagram, TikTok, X, SoundCloud "
                            "and 1000+ other sites.")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))

        self._build_url_card().grid(row=1, column=0, sticky="ew")
        self._build_preview_card().grid(row=2, column=0, sticky="nsew", pady=14)
        self._build_options_card().grid(row=3, column=0, sticky="ew")
        self.reset(focus=False)

    # ------------------------------------------------------------------ layout
    def _build_url_card(self) -> Card:
        card = Card(self)
        card.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        row.grid_columnconfigure(0, weight=1)

        field = ctk.CTkFrame(row, fg_color=T.FIELD, border_color=T.FIELD_BORDER, border_width=1,
                             corner_radius=10, height=46)
        field.grid(row=0, column=0, sticky="ew")
        field.grid_columnconfigure(1, weight=1)
        T.glyph(field, T.Icon.LINK, 16, T.TEXT_3, width=20).grid(row=0, column=0, padx=(14, 4))
        self.url_entry = ctk.CTkEntry(
            field, height=42, border_width=0, fg_color=T.FIELD, text_color=T.TEXT,
            placeholder_text="https://www.youtube.com/watch?v=...",
            placeholder_text_color=T.TEXT_3, font=T.font(14))
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=2)
        self.clear_btn = T.icon_button(field, T.Icon.CLOSE, self._clear_url, size=30)
        self._field = field

        self.paste_btn = T.ghost_button(row, "Paste", self.paste_and_fetch, icon=T.Icon.PASTE,
                                        height=46, width=96, size=13)
        self.paste_btn.grid(row=0, column=1, padx=(10, 0))
        self.fetch_btn = T.accent_button(row, "Fetch", self.fetch, icon=T.Icon.SEARCH,
                                         height=46, width=108, size=13)
        self.fetch_btn.grid(row=0, column=2, padx=(10, 0))

        meta = ctk.CTkFrame(card, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 14))
        self.platform_chip = Chip(meta, "", "accent")
        self.url_hint = T.label(meta, "", 12, color=T.TEXT_3)
        self.url_hint.grid(row=0, column=1, sticky="w")

        inner = self.url_entry._entry  # the tkinter.Entry inside CTkEntry
        inner.bind("<KeyRelease>", lambda _e: self._on_url_changed(), add="+")
        inner.bind("<<Paste>>", lambda _e: self.after(30, self._on_pasted), add="+")
        inner.bind("<FocusIn>", lambda _e: field.configure(border_color=T.ACCENT), add="+")
        inner.bind("<FocusOut>", lambda _e: field.configure(border_color=T.FIELD_BORDER), add="+")
        return card

    def _build_preview_card(self) -> Card:
        card = Card(self)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        self.preview_card = card

        # empty / loading / error state
        self.state_box = ctk.CTkFrame(card, fg_color="transparent")
        self.state_box.grid_columnconfigure(0, weight=1)
        self.state_icon = T.glyph(self.state_box, T.Icon.DOWNLOAD, 30, T.TEXT_3)
        self.state_icon.grid(row=0, column=0, pady=(0, 6))
        self.state_title = T.label(self.state_box, "", 15, "semibold", anchor="center",
                                   justify="center")
        self.state_title.grid(row=1, column=0)
        self.state_text = T.label(self.state_box, "", 12, color=T.TEXT_2, anchor="center",
                                  justify="center", wraplength=560)
        self.state_text.grid(row=2, column=0, pady=(2, 0))
        self.state_progress = T.progress_bar(self.state_box, width=220, mode="indeterminate")
        self.state_action = T.ghost_button(self.state_box, "Open Settings",
                                           lambda: self.app.show_page("settings"),
                                           icon=T.Icon.SETTINGS)

        # loaded media
        self.media_box = ctk.CTkFrame(card, fg_color="transparent")
        self.media_box.grid_columnconfigure(1, weight=1)
        blank = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
        self._thumb_placeholder = ctk.CTkImage(light_image=blank, dark_image=blank,
                                               size=(THUMB_W, THUMB_H))
        self.thumb = ctk.CTkLabel(self.media_box, text="", width=THUMB_W, height=THUMB_H,
                                  fg_color=T.FIELD, corner_radius=10,
                                  image=self._thumb_placeholder, compound="center")
        self.thumb.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(14, 20), pady=14)
        chips = ctk.CTkFrame(self.media_box, fg_color="transparent")
        chips.grid(row=0, column=1, sticky="w", pady=(18, 8), padx=(0, 18))
        self.site_chip = Chip(chips, "", "accent")
        self.site_chip.grid(row=0, column=0, padx=(0, 6))
        self.kind_chip = Chip(chips, "", "neutral")
        self.playlist_chip = Chip(chips, "", "warning")
        self.title_label = T.label(self.media_box, "", 16, "semibold", wraplength=460)
        self.title_label.grid(row=1, column=1, sticky="nw", padx=(0, 18))
        self.byline = T.label(self.media_box, "", 13, color=T.TEXT_2)
        self.byline.grid(row=2, column=1, sticky="nw", padx=(0, 18), pady=(6, 0))
        self.media_box.bind("<Configure>", self._on_media_resize, add="+")
        return card

    def _build_options_card(self) -> Card:
        card = Card(self)
        card.grid_columnconfigure(0, weight=1)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        body.grid_columnconfigure(1, weight=1)

        T.label(body, "Format", 12, "semibold", T.TEXT_2).grid(row=0, column=0, sticky="w",
                                                              pady=(0, 6))
        self.tiles = TileSelector(body, FORMATS, command=self._on_kind_changed)
        self.tiles.grid(row=1, column=0, sticky="w")

        self.quality_title = T.label(body, "Quality", 12, "semibold", T.TEXT_2)
        self.quality_title.grid(row=0, column=2, sticky="w", pady=(0, 6), padx=(20, 0))
        self.quality_menu = T.option_menu(body, ["Best available"], width=200,
                                          command=self._on_quality_picked)
        self.quality_menu.grid(row=1, column=2, sticky="w", padx=(20, 0))
        self.playlist_switch = T.switch(body, "Whole playlist")

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 16))
        footer.grid_columnconfigure(1, weight=1)
        T.glyph(footer, T.Icon.FOLDER, 14, T.TEXT_3, width=18).grid(row=0, column=0, padx=(0, 6))
        self.dest_label = T.label(footer, "", 12, color=T.TEXT_2)
        self.dest_label.grid(row=0, column=1, sticky="w")
        T.ghost_button(footer, "Change", self.app.choose_download_dir, height=30,
                       width=74).grid(row=0, column=2, padx=(8, 12))
        self.download_btn = T.accent_button(footer, "Download", self.download,
                                            icon=T.Icon.DOWNLOAD, height=44, width=170, size=14)
        self.download_btn.grid(row=0, column=3)
        return card

    # ------------------------------------------------------------------ state helpers
    def _show_state(self, kind: str, title: str, text: str = "", action: bool = False) -> None:
        self.media_box.grid_forget()
        self.state_box.grid(row=0, column=0, padx=24, pady=24)
        glyph, color = {
            "empty": (T.Icon.DOWNLOAD, T.TEXT_3),
            "loading": (T.Icon.SEARCH, T.ACCENT_TEXT),
            "error": (T.Icon.ERROR, T.DANGER),
        }[kind]
        self.state_icon.configure(text=glyph, text_color=color)
        self.state_title.configure(text=title)
        self.state_text.configure(text=text)
        if kind == "loading":
            self.state_progress.grid(row=3, column=0, pady=(14, 0))
            self.state_progress.start()
        else:
            self.state_progress.stop()
            self.state_progress.grid_forget()
        if action:
            self.state_action.grid(row=4, column=0, pady=(14, 0))
        else:
            self.state_action.grid_forget()

    def _show_media(self, info: MediaInfo) -> None:
        self.state_progress.stop()
        self.state_box.grid_forget()
        self.media_box.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        site = pretty_extractor(info.extractor) or detect_platform(info.webpage_url or info.url)
        if site:
            self.site_chip.set(site)
            self.site_chip.grid(row=0, column=0, padx=(0, 6))
        else:
            self.site_chip.grid_forget()
        self.kind_chip.set("Video" if info.has_video else "Audio")
        self.kind_chip.grid(row=0, column=1, padx=(0, 6))
        if info.is_playlist:
            self.playlist_chip.set(f"Playlist · {info.entry_count} items")
            self.playlist_chip.grid(row=0, column=2)
        else:
            self.playlist_chip.grid_forget()
        self.title_label.configure(text=truncate(info.title or "Untitled", 160))
        parts = [p for p in (info.uploader, fmt_duration(info.duration)) if p]
        self.byline.configure(text="   •   ".join(parts))
        self._clear_thumb(T.Icon.VIDEO if info.has_video else T.Icon.MUSIC)
        if info.thumbnail:
            token = self._fetch_token
            threading.Thread(target=self._load_thumb, args=(info.thumbnail, token),
                             daemon=True, name="thumbnail").start()

    def _clear_thumb(self, glyph: str = "") -> None:
        # Swap to a transparent placeholder *before* dropping the old image: customtkinter
        # errors if a label still references a garbage-collected PhotoImage.
        self.thumb.configure(image=self._thumb_placeholder, text=glyph, fg_color=T.FIELD,
                             font=T.icon_font(26), text_color=T.TEXT_3)
        self._thumb_image = None

    def _on_media_resize(self, event: tkinter.Event) -> None:
        # Debounced: a window drag produces a burst of <Configure> events.
        self._media_width = event.width
        if self._wrap_job is None:
            self._wrap_job = self.after(60, self._apply_wrap)

    def _apply_wrap(self) -> None:
        self._wrap_job = None
        scaling = self._get_widget_scaling() or 1.0
        width = int(max(200, self._media_width / scaling - THUMB_W - 70))
        if abs(width - int(self.title_label.cget("wraplength") or 0)) > 4:
            self.title_label.configure(wraplength=width)

    def _load_thumb(self, url: str, token: int) -> None:
        try:
            img = fetch_thumbnail(url, (THUMB_W * 2, THUMB_H * 2), radius=20)
        except Exception as exc:  # noqa: BLE001 - thumbnail is cosmetic
            log.info("thumbnail failed: %s", exc)
            return
        self.app.post(self._set_thumb, img, token)

    def _set_thumb(self, img: Any, token: int) -> None:
        if token != self._fetch_token or self.info is None:
            return
        self._thumb_image = ctk.CTkImage(light_image=img, dark_image=img, size=(THUMB_W, THUMB_H))
        self.thumb.configure(image=self._thumb_image, text="", fg_color="transparent")

    # ------------------------------------------------------------------ url handling
    def _url(self) -> str:
        return self.url_entry.get().strip()

    def set_url(self, text: str) -> None:
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text.strip())
        self._on_url_changed()

    def _clear_url(self) -> None:
        self.reset()

    def _on_url_changed(self) -> None:
        url = self._url()
        if url:
            self.clear_btn.grid(row=0, column=2, padx=(0, 8))
        else:
            self.clear_btn.grid_forget()
        platform = detect_platform(url) if url else None
        if platform:
            self.platform_chip.set(platform)
            self.platform_chip.grid(row=0, column=0, padx=(0, 10))
            self.url_hint.configure(text="Press Enter to fetch details, or Ctrl+D to download "
                                         "right away.")
        else:
            self.platform_chip.grid_forget()
            self.url_hint.configure(
                text="Tip: Ctrl+V pastes, Enter fetches, Ctrl+D downloads, F5 starts over."
                if not url else "That doesn't look like a link yet.")

    def _on_pasted(self) -> None:
        self._on_url_changed()
        url = self._url()
        if looks_like_url(url) and (self.info is None or normalize_url(url) != self._info_url):
            self.fetch()

    def paste_and_fetch(self) -> None:
        try:
            text = self.clipboard_get()
        except tkinter.TclError:
            text = ""
        text = text.strip()
        if not text:
            self.app.toast("The clipboard is empty - copy a video link first.", "warning")
            return
        self.set_url(text.splitlines()[0])
        self.url_entry.focus()
        self.url_entry._entry.icursor("end")
        if looks_like_url(self._url()):
            self.fetch()

    # ------------------------------------------------------------------ fetch
    def fetch(self) -> None:
        raw = self._url()
        if not raw:
            self.app.toast("Paste a link first.", "warning")
            self.url_entry.focus()
            return
        if not looks_like_url(raw):
            self._show_state("error", "That doesn't look like a link",
                             "Copy the address of a video, song or playlist and paste it here.")
            return
        url = normalize_url(raw)
        if self._fetch_cancel is not None:
            self._fetch_cancel.set()
        self._fetch_token += 1
        token = self._fetch_token
        cancel = threading.Event()
        self._fetch_cancel = cancel
        self.info = None
        self._refresh_quality()  # drop the previous video's heights right away
        self._set_fetching(True)
        site = detect_platform(url)
        self._show_state("loading", "Fetching details…",
                         f"Reading {site or 'the page'} - this usually takes a few seconds.")
        cookies = self.app.cfg.get("cookies_browser")
        threading.Thread(target=self._fetch_worker, args=(url, cookies, cancel, token),
                         daemon=True, name="fetch-info").start()

    def _fetch_worker(self, url: str, cookies: str | None, cancel: threading.Event,
                      token: int) -> None:
        try:
            info = fetch_info(url, self.app.deps, cookies_browser=cookies, cancel_event=cancel)
        except EngineError as exc:
            self.app.post(self._fetch_failed, token, friendly_error(exc), cancel)
        except Exception as exc:  # noqa: BLE001 - shown to the user, logged for debugging
            log.exception("fetch_info failed")
            self.app.post(self._fetch_failed, token,
                          f"Couldn't read this link: {friendly_error(exc)}", cancel)
        else:
            self.app.post(self._fetch_done, token, info, url)

    def _fetch_done(self, token: int, info: MediaInfo, url: str) -> None:
        if token != self._fetch_token:
            return
        self._set_fetching(False)
        self.info = info
        self._info_url = url
        self._show_media(info)
        self.tiles.set_visible(["mp4", "mp3", "m4a"] if info.has_video else ["mp3", "m4a"])
        if info.is_playlist:
            self.playlist_switch.configure(text=f"Whole playlist ({info.entry_count} items)")
            self.playlist_switch.grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))
            self.playlist_switch.select()
        else:
            self.playlist_switch.deselect()
            self.playlist_switch.grid_forget()
        self._refresh_quality()

    def _fetch_failed(self, token: int, message: str, cancel: threading.Event) -> None:
        if token != self._fetch_token or cancel.is_set():
            return
        self._set_fetching(False)
        low = message.lower()
        needs_settings = any(w in low for w in ("cookie", "login", "log in", "sign in", "private"))
        self._show_state("error", "Couldn't load this link", message, action=needs_settings)

    def _set_fetching(self, busy: bool) -> None:
        self._fetching = busy
        self.fetch_btn.configure(text="Fetching" if busy else "Fetch",
                                 state="disabled" if busy else "normal")

    # ------------------------------------------------------------------ options
    def _on_kind_changed(self, value: str) -> None:
        """User clicked a format tile (programmatic ``tiles.set`` does not come here)."""
        self._user_kind = value
        self._refresh_quality()

    def _on_quality_picked(self, label: str) -> None:
        """User picked an entry in the quality/bitrate dropdown."""
        kind = self._quality_kind
        value = self._quality_map.get(label)
        if value is None or kind not in ("mp4", "mp3"):
            return
        self._user_quality[kind] = value
        self._user_kind = kind  # choosing a quality also commits to this format
        log.debug("quality picked: kind=%s value=%s (%s)", kind, value, label)

    def _wanted_quality(self, kind: str) -> str:
        """The user's explicit choice for ``kind`` if any, else the Settings default."""
        if kind in self._user_quality:
            return self._user_quality[kind]
        if kind == "mp3":
            return str(self.app.cfg.get("mp3_bitrate", "320"))
        return str(self.app.cfg.get("default_quality", "best"))

    def _refresh_quality(self) -> None:
        kind = self.tiles.get() or "mp4"
        self._quality_kind = kind
        if kind == "mp4":
            heights = self.info.heights if self.info is not None else None
            values = quality_options(heights)
            self._quality_map = {quality_label(v): v for v in values}
            # An explicit "720" stays 720 when the new list has it; if the video lacks that
            # height the closest lower one is shown (never silently "Best").
            chosen = preferred_quality(self._wanted_quality("mp4"), values)
            self.quality_title.configure(text="Quality")
            self._set_menu(list(self._quality_map), quality_label(chosen), True)
        elif kind == "mp3":
            self._quality_map = {lbl: v for v, lbl in BITRATE_LABELS.items()}
            self.quality_title.configure(text="Bitrate")
            want = self._wanted_quality("mp3")
            self._set_menu(list(self._quality_map),
                           BITRATE_LABELS.get(want, BITRATE_LABELS["320"]), True)
        else:
            self._quality_map = {"Best available": "best"}
            self.quality_title.configure(text="Quality")
            self._set_menu(["Best available"], "Best available", False)

    def selected_options(self) -> tuple[str, str, str]:
        """``(kind, quality, mp3_bitrate)`` exactly as currently shown in the UI.

        The label shown in the menu is the source of truth. If it can't be mapped (should
        not happen) the user's explicit choice / Settings default is used and a warning is
        logged, rather than silently downloading "best".
        """
        kind = self.tiles.get() or "mp4"
        if self._quality_kind != kind:  # tiles changed without the command (defensive)
            self._refresh_quality()
        label = self.quality_menu.get()
        value = self._quality_map.get(label)
        if value is None:
            value = self._wanted_quality(kind) if kind in ("mp4", "mp3") else "best"
            log.warning("quality label %r not in menu for %s; using %s", label, kind, value)
        quality = value if kind == "mp4" else "best"
        bitrate = value if kind == "mp3" else str(self.app.cfg.get("mp3_bitrate", "320"))
        if kind == "mp3" and bitrate not in BITRATE_LABELS:
            bitrate = "320"
        return kind, quality, bitrate

    def _set_menu(self, values: list[str], value: str, enabled: bool) -> None:
        """Reconfigure the quality menu only when something actually changed.

        CTkOptionMenu.configure() forces a full redraw + idle flush, which is the most
        expensive call on this page, so skipping no-op updates keeps the UI snappy.
        """
        state = "normal" if enabled else "disabled"
        if values != self._menu_values or state != self._menu_state:
            self._menu_values, self._menu_state = list(values), state
            self.quality_menu.configure(values=values, state=state)
        if self.quality_menu.get() != value:
            self.quality_menu.set(value)

    def update_destination(self) -> None:
        text = truncate(str(self.app.cfg.get("download_dir", "")), 70)
        if text != self.dest_label.cget("text"):
            self.dest_label.configure(text=text)

    def apply_defaults(self) -> None:
        """Re-apply default format/quality from settings (used on reset and settings change).

        Explicit choices the user made on this page are kept; ``reset()`` clears them first.
        """
        visible = ["mp4", "mp3", "m4a"] if self.info is None or self.info.has_video \
            else ["mp3", "m4a"]
        self.tiles.set_visible(visible)
        kind = self._user_kind or str(self.app.cfg.get("default_kind", "mp4"))
        if kind not in visible:
            kind = visible[0]
        if self.tiles.get() != kind:
            self.tiles.set(kind)
        self._refresh_quality()
        self.update_destination()

    # ------------------------------------------------------------------ download
    def download(self) -> None:
        raw = self._url()
        info = self.info
        if not raw and info is not None:
            url = self._info_url
        elif not raw:
            self.app.toast("Paste a link first.", "warning")
            self.url_entry.focus()
            return
        elif not looks_like_url(raw):
            self.app.toast("That doesn't look like a link.", "error")
            return
        else:
            url = normalize_url(raw)
        if info is not None and url not in (self._info_url, info.url, info.webpage_url):
            info = None  # the user changed the link after fetching

        kind, quality, bitrate = self.selected_options()
        out_dir = str(self.app.cfg.get("download_dir") or "")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            self.app.toast(f"Can't use the download folder: {friendly_error(exc)}", "error",
                           "Change folder", self.app.choose_download_dir)
            return
        options = JobOptions(
            kind=kind,
            quality=quality,
            mp3_bitrate=bitrate,
            out_dir=out_dir,
            playlist=bool(info and info.is_playlist and self.playlist_switch.get()),
            cookies_browser=self.app.cfg.get("cookies_browser"),
        )
        if info is not None:
            title = info.title or url
            if options.playlist:
                title = f"{title} ({info.entry_count} items)"
        else:
            site = detect_platform(url)
            title = f"{site} link" if site else url
            title = f"{title} — {truncate(url, 60)}" if site else title
        log.info("download requested: url=%s menu=%r -> %s", url, self.quality_menu.get(), options)
        try:
            self.app.queue_page.add(url, title, options, has_video=kind == "mp4")
        except DuplicateJobError:
            # Same link + format + quality is already waiting/running: don't start a second
            # identical job (it would race for the same output file).
            self.app.toast("Already in the queue", "info", "View queue",
                           lambda: self.app.show_page("queue"))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("could not create job")
            self.app.toast(f"Couldn't start the download: {friendly_error(exc)}", "error")
            return
        self.app.toast(f"Added to queue: {truncate(title, 60)}", "success", "View queue",
                       lambda: self.app.show_page("queue"))
        self.reset()

    def reset(self, focus: bool = True) -> None:
        if self._fetch_cancel is not None:
            self._fetch_cancel.set()
            self._fetch_cancel = None
        self._fetch_token += 1
        self._set_fetching(False)
        self.info = None
        self._user_kind = None  # a fresh start goes back to the Settings defaults
        self._user_quality.clear()
        self._clear_thumb()
        self.url_entry.delete(0, "end")
        self._on_url_changed()
        self.playlist_switch.grid_forget()
        self.playlist_switch.deselect()
        self._show_state("empty", "Ready when you are",
                         "Paste a link above and press Fetch to preview it, then choose a "
                         "format and download.")
        self.apply_defaults()
        if focus:
            self.url_entry.focus()
