"""Main window: sidebar navigation, pages, toasts, shortcuts and startup flow."""
from __future__ import annotations

import logging
import os
import queue
import sys
import time
import tkinter
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

from ui import APP_NAME, AUTHOR, GITHUB_PROFILE_URL, WEBSITE_URL
from ui import theme as T
from ui.backend import (FAKE_BACKEND, DependencyManager, ensure_dirs, load_config, resource_path,
                        save_config)
from ui.transition import (SYSTEM_POLL_MS, ThemeTransition, apply_appearance, paint_now,
                           resolve_mode, set_cloaked, set_title_bar_dark)
from ui.util import friendly_error
from ui.widgets import Dot, LinkLabel, NavItem, SlidingIndicator, Toast, bind_tree, set_cursor

log = logging.getLogger(__name__)

APP_USER_MODEL_ID = "h190k.downloader.2"
UPDATE_CHECK_INTERVAL = 24 * 3600
POLL_MS = 30

NAV = (
    ("download", T.Icon.DOWNLOAD, "Download"),
    ("queue", T.Icon.QUEUE, "Queue"),
    ("settings", T.Icon.SETTINGS, "Settings"),
    ("about", T.Icon.INFO, "About & updates"),
)


class App(ctk.CTk):
    # customtkinter recolors the title bar by withdrawing and re-showing the whole window on every
    # appearance change (a visible blink); we set the DWM attribute directly instead.
    _deactivate_windows_window_header_manipulation = True

    def __init__(self) -> None:
        self.cfg: dict[str, Any] = load_config()
        # Resolve "system" ourselves and always hand customtkinter an explicit mode: its own
        # system-follow loop would recolor the window without our cross-fade.
        mode = resolve_mode(self.cfg.get("theme"))
        ctk.set_appearance_mode(mode)
        super().__init__(fg_color=T.BG)
        self.withdraw()  # build hidden (no white flash / half-drawn layout), reveal when ready
        self.theme_fx = ThemeTransition(self, self._apply_mode)
        self.theme_fx.mode = mode
        set_title_bar_dark(self, mode == "dark")  # before the window is ever shown
        self.title(APP_NAME)
        self._set_icon()
        self._place_window(1040, 700)
        self.minsize(920, 640)

        self._calls: queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...]]] = (
            queue.SimpleQueue())
        self._closing = False
        self.current_page = ""
        self.updating_tools = False
        self._tools_state = "unknown"
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav: dict[str, NavItem] = {}
        self.main: ctk.CTkFrame | None = None
        self.setup: Any = None

        try:
            ensure_dirs()
        except OSError as exc:
            log.warning("ensure_dirs failed: %s", exc)
        self.deps = DependencyManager()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_job: str | None = self.after(POLL_MS, self._drain)
        self._theme_job: str | None = self.after(SYSTEM_POLL_MS, self._watch_system_theme)

        try:
            ready = self.deps.is_ready()
        except Exception:  # noqa: BLE001
            ready = False
        if ready:
            self._build_main()
            self.after(600, self._startup_checks)
        else:
            self._show_setup()
        self.after(30, self._reveal)

    def _reveal(self) -> None:
        self.update_idletasks()
        set_title_bar_dark(self, self.theme_fx.mode == "dark")
        # Map the window while it is cloaked, let Tk paint it completely, then uncloak: otherwise
        # Windows shows the unpainted (white) client area for a few hundred ms on startup.
        cloaked = set_cloaked(self, True)
        self.deiconify()
        self.lift()
        if cloaked:
            safety = self.after(1500, lambda: set_cloaked(self, False))  # never stay hidden
            try:
                paint_now(self)
            finally:
                set_cloaked(self, False)
                try:
                    self.after_cancel(safety)
                except tkinter.TclError:
                    pass

    # ------------------------------------------------------------------ window
    def _set_icon(self) -> None:
        try:
            icon = resource_path("assets/icon.ico")
            if os.path.isfile(icon):
                self.iconbitmap(default=str(icon))
        except (tkinter.TclError, OSError) as exc:
            log.info("icon not set: %s", exc)

    def _place_window(self, w: int, h: int) -> None:
        # CTk scales geometry by the DPI factor; center using logical screen size.
        scale = self._get_window_scaling()
        sw, sh = self.winfo_screenwidth() / scale, self.winfo_screenheight() / scale
        w, h = int(min(w, sw - 40)), int(min(h, sh - 80))
        x, y = int((sw - w) / 2), int(max(0, (sh - h) / 2 - 20))
        self.geometry(f"{w}x{h}+{int(x * scale)}+{int(y * scale)}")

    # ------------------------------------------------------------------ thread marshaling
    def post(self, func: Callable[..., Any], *args: Any) -> None:
        """Run ``func(*args)`` on the Tk thread. Safe to call from any thread."""
        self._calls.put((func, args))

    def _drain(self) -> None:
        deadline = time.perf_counter() + 0.05
        while time.perf_counter() < deadline:
            try:
                func, args = self._calls.get_nowait()
            except queue.Empty:
                break
            try:
                func(*args)
            except Exception:  # noqa: BLE001 - never let a UI callback kill the loop
                log.exception("UI callback failed")
        self._drain_job = None if self._closing else self.after(POLL_MS, self._drain)

    def destroy(self) -> None:
        self._closing = True
        for attr in ("_drain_job", "_theme_job"):
            job = getattr(self, attr, None)
            setattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
        theme_fx = getattr(self, "theme_fx", None)
        if theme_fx is not None:
            theme_fx.cancel()
        toaster = getattr(self, "toaster", None)
        if toaster is not None:
            toaster.hide()
        super().destroy()

    # ------------------------------------------------------------------ setup flow
    def _show_setup(self) -> None:
        from ui.setup_screen import SetupScreen

        self.setup = SetupScreen(self, self)
        self.setup.grid(row=0, column=0, sticky="nsew")
        self.after(400, self.setup.start)

    def finish_setup(self, skipped: bool = False) -> None:
        if self.setup is not None:
            self.setup.destroy()
            self.setup = None
        self._build_main()
        if skipped:
            self.set_tools_indicator("missing")
            self.toast("Some tools are missing, so downloads won't work yet. Install them from "
                       "About & updates.", "warning", "Open", lambda: self.show_page("about"))
        self.after(600, self._startup_checks)

    # ------------------------------------------------------------------ main UI
    def _build_main(self) -> None:
        from ui.about_page import AboutPage
        from ui.download_page import DownloadPage
        from ui.queue_page import QueuePage
        from ui.settings_page import SettingsPage

        main = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        main.grid(row=0, column=0, sticky="nsew")
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.main = main

        self._build_sidebar(main).grid(row=0, column=0, sticky="nsw")

        content = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self.content = content

        # Queue page first: other pages reference it.
        self.queue_page = QueuePage(content, self)
        self.download_page = DownloadPage(content, self)
        self.settings_page = SettingsPage(content, self)
        self.about_page = AboutPage(content, self)
        self.pages = {"download": self.download_page, "queue": self.queue_page,
                      "settings": self.settings_page, "about": self.about_page}
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew", padx=32, pady=(26, 24))
        self.toaster = Toast(content)
        self.show_page("download")
        self._bind_shortcuts()
        self.after(150, lambda: self.download_page.url_entry.focus())

    def _build_sidebar(self, master: Any) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(master, width=232, fg_color=T.SIDEBAR, corner_radius=0)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(2, weight=1)
        edge = ctk.CTkFrame(bar, width=1, fg_color=T.SIDEBAR_BORDER, corner_radius=0)
        edge.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 22))
        self._logo = T.load_app_logo(34)
        ctk.CTkLabel(brand, text="", image=self._logo, width=34, height=34).grid(
            row=0, column=0, rowspan=2, padx=(0, 10))
        T.label(brand, "H190K", 16, "bold", height=20).grid(row=0, column=1, sticky="sw")
        T.label(brand, "Downloader", 12, color=T.TEXT_2, height=16).grid(row=1, column=1,
                                                                        sticky="nw")

        nav = ctk.CTkFrame(bar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=12)
        nav.grid_columnconfigure(0, weight=1)
        T.label(nav, "MENU", 10, "semibold", T.TEXT_3).grid(row=0, column=0, sticky="w",
                                                          padx=14, pady=(0, 6))
        for i, (key, glyph, text) in enumerate(NAV, start=1):
            item = NavItem(nav, glyph, text, lambda k=key: self.show_page(k))
            item.grid(row=i, column=0, sticky="ew", pady=2)
            self.nav[key] = item
        self.nav_indicator = SlidingIndicator(nav)

        # tools status indicator
        status = ctk.CTkFrame(bar, fg_color=T.FIELD, corner_radius=10, height=54)
        status.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        status.grid_columnconfigure(1, weight=1)
        self.tools_dot = Dot(status, T.TEXT_3, 9)
        self.tools_dot.grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=12)
        self.tools_title = T.label(status, "Checking tools…", 12, "semibold", height=18)
        self.tools_title.grid(row=0, column=1, sticky="sw", pady=(10, 0))
        self.tools_sub = T.label(status, "", 11, color=T.TEXT_2, height=16)
        self.tools_sub.grid(row=1, column=1, sticky="nw", pady=(0, 10))
        bind_tree(status, "<Button-1>", lambda _e: self.show_page("about"))
        set_cursor(status, "hand2")

        foot = ctk.CTkFrame(bar, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 18))
        T.label(foot, "Created by", 11, color=T.TEXT_3).grid(row=0, column=0)
        LinkLabel(foot, AUTHOR.lower(), WEBSITE_URL, 11, weight="semibold").grid(
            row=0, column=1, padx=(3, 0))
        T.label(foot, "·", 11, color=T.TEXT_3).grid(row=0, column=2, padx=6)
        LinkLabel(foot, "GitHub", GITHUB_PROFILE_URL, 11, weight="semibold").grid(row=0, column=3)
        return bar

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        page.tkraise()
        self.toaster.lift()  # keep a visible toast above the newly raised page
        self.current_page = key
        for k, item in self.nav.items():
            item.set_selected(k == key)
        self.nav_indicator.move_to(self.nav[key])
        if key == "download":
            self.after(10, lambda: self.download_page.url_entry.focus())
        else:
            self.focus_set()
        if key == "about" and not self.about_page.status:
            self.about_page.refresh_status()

    def set_queue_badge(self, count: int) -> None:
        if "queue" in self.nav:
            self.nav["queue"].set_badge(count)

    # ------------------------------------------------------------------ toasts
    def toast(self, message: str, kind: str = "info", action_text: str | None = None,
              action: Callable[[], Any] | None = None) -> None:
        toaster = getattr(self, "toaster", None)
        if toaster is None:
            log.info("%s: %s", kind, message)
            return
        toaster.show(message, kind, action_text, action)

    # ------------------------------------------------------------------ config
    def set_config(self, **changes: Any) -> None:
        self.cfg.update(changes)
        try:
            save_config(self.cfg)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Couldn't save settings: {friendly_error(exc)}", "error")
        if {"default_kind", "default_quality", "mp3_bitrate"} & changes.keys():
            if getattr(self, "download_page", None) and self.download_page.info is None:
                self.download_page.apply_defaults()
        if "download_dir" in changes:
            self.download_page.update_destination()
            self.settings_page.refresh()

    def apply_theme(self) -> None:
        """Apply the ``theme`` setting with a cross-fade (see :mod:`ui.transition`)."""
        self.theme_fx.switch(resolve_mode(self.cfg.get("theme")))

    def _apply_mode(self, mode: str) -> None:
        apply_appearance(self, mode)

    def _watch_system_theme(self) -> None:
        """Follow the Windows app theme while the setting is "System"."""
        self._theme_job = None
        if self._closing:
            return
        if self.cfg.get("theme") == "system":
            mode = resolve_mode("system")
            if mode != self.theme_fx.mode:
                self.theme_fx.switch(mode)
        self._theme_job = self.after(SYSTEM_POLL_MS, self._watch_system_theme)

    def choose_download_dir(self) -> None:
        current = str(self.cfg.get("download_dir") or "")
        path = filedialog.askdirectory(parent=self, title="Choose download folder",
                                       initialdir=current if os.path.isdir(current) else None,
                                       mustexist=False)
        if path:
            self.set_config(download_dir=os.path.normpath(path))
            self.toast("Download folder updated.", "success")

    # ------------------------------------------------------------------ tools indicator
    def set_tools_indicator(self, state: str) -> None:
        self._tools_state = state
        styles = {
            "ready": (T.SUCCESS, "Tools ready", "yt-dlp, FFmpeg, Deno"),
            "updates": (T.WARNING, "Update available", "Click to update tools"),
            "missing": (T.DANGER, "Tools missing", "Click to install"),
            "checking": (T.ACCENT, "Checking for updates", "Running in background"),
            "updating": (T.ACCENT, "Updating tools…", "Please wait"),
            "unknown": (T.TEXT_3, "Checking tools…", ""),
        }
        color, title, sub = styles.get(state, styles["unknown"])
        if not hasattr(self, "tools_dot"):
            return
        self.tools_dot.set_color(color)
        self.tools_title.configure(text=title)
        self.tools_sub.configure(text=sub)

    def refresh_tools_indicator(self) -> None:
        if self.updating_tools:
            self.set_tools_indicator("updating")
            return
        try:
            ready = self.deps.is_ready()
        except Exception:  # noqa: BLE001
            ready = False
        if not ready:
            self.set_tools_indicator("missing")
        elif any(v.get("update_available") for v in self.about_page.updates.values()):
            self.set_tools_indicator("updates")
        else:
            self.set_tools_indicator("ready")

    def _startup_checks(self) -> None:
        # Show the result of the last check (within the 24h throttle window) immediately.
        cached = self.cfg.get("last_update_info")
        if isinstance(cached, dict) and all(isinstance(v, dict) for v in cached.values()):
            self.about_page.set_updates(cached)
        self.about_page.refresh_status()
        self.refresh_tools_indicator()
        last = float(self.cfg.get("last_update_check") or 0)
        if self.cfg.get("auto_check_updates", True) and time.time() - last > UPDATE_CHECK_INTERVAL:
            self.about_page.check_updates(silent=True)

    # ------------------------------------------------------------------ shortcuts
    def _bind_shortcuts(self) -> None:
        for seq in ("<Control-v>", "<Control-V>"):
            self.bind(seq, self._sc_paste, add="+")
        for seq in ("<Control-d>", "<Control-D>"):
            self.bind(seq, self._sc_download, add="+")
        self.bind("<Return>", self._sc_enter, add="+")
        self.bind("<KP_Enter>", self._sc_enter, add="+")
        self.bind("<F5>", self._sc_reset, add="+")

    def _typing_elsewhere(self) -> bool:
        w = self.focus_get()
        url_field = self.download_page.url_entry._entry
        return isinstance(w, (tkinter.Entry, tkinter.Text)) and w is not url_field

    def _sc_paste(self, _e: Any = None) -> str | None:
        w = self.focus_get()
        if isinstance(w, (tkinter.Entry, tkinter.Text)):
            return None  # native paste already handled by the focused field
        self.show_page("download")
        self.download_page.paste_and_fetch()
        return "break"

    def _sc_enter(self, _e: Any = None) -> str | None:
        if self.current_page != "download" or self._typing_elsewhere():
            return None
        if isinstance(self.focus_get(), (tkinter.Button,)):
            return None
        self.download_page.fetch()
        return "break"

    def _sc_download(self, _e: Any = None) -> str:
        self.show_page("download") if self.current_page != "download" else None
        self.download_page.download()
        return "break"

    def _sc_reset(self, _e: Any = None) -> str:
        self.show_page("download")
        self.download_page.reset()
        return "break"

    # ------------------------------------------------------------------ closing
    def _on_close(self) -> None:
        queue_page = getattr(self, "queue_page", None)
        running = queue_page.pending_count() if queue_page is not None else 0
        if running and queue_page is not None:
            if not messagebox.askyesno(
                    APP_NAME, f"{running} download{'s are' if running > 1 else ' is'} still in "
                              "progress.\n\nCancel and quit?", icon="warning", parent=self):
                return
            queue_page.cancel_all()
        if self.setup is not None and self.setup.running:
            if not messagebox.askyesno(APP_NAME, "Setup is still running. Quit anyway?",
                                       icon="warning", parent=self):
                return
        self._closing = True
        dp = getattr(self, "download_page", None)
        if dp is not None and dp._fetch_cancel is not None:
            dp._fetch_cancel.set()
        self.after(150 if running else 0, self.destroy)


def _set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def run_gui() -> None:
    """Start the desktop application (blocks until the window is closed)."""
    logging.basicConfig(level=logging.INFO if FAKE_BACKEND else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _set_app_user_model_id()
    ctk.set_default_color_theme("blue")
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
