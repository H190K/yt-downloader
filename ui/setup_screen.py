"""First-run setup: installs the missing tools with per-tool progress."""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ui import APP_NAME
from ui import theme as T
from ui.backend import TOOLS, TOOLS_DIR, DependencyError
from ui.util import friendly_error, truncate
from ui.widgets import Card, Chip

if TYPE_CHECKING:
    from ui.app import App

log = logging.getLogger(__name__)

TOOL_TEXT = {
    "yt-dlp": ("yt-dlp", "The download engine"),
    "ffmpeg": ("FFmpeg", "Merges video + audio and converts to MP3"),
    "deno": ("Deno", "JavaScript runtime YouTube requires"),
}


class SetupScreen(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App") -> None:
        super().__init__(master, fg_color=T.BG, corner_radius=0)
        self.app = app
        self.running = False
        self._current: str | None = None
        self._rows: dict[str, dict[str, Any]] = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        card = Card(self, corner_radius=16)
        card.grid(row=0, column=0)
        card.grid_columnconfigure(0, weight=1)
        self._logo = T.load_app_logo(56)
        ctk.CTkLabel(card, text="", image=self._logo, width=56, height=56).grid(
            row=0, column=0, pady=(32, 10))
        T.label(card, f"Welcome to {APP_NAME}", 22, "semibold", anchor="center").grid(
            row=1, column=0, padx=40)
        T.label(card, "Before your first download the app needs three free tools. They are "
                      "downloaded once (about 150 MB) into the app folder and kept up to date "
                      "for you.", 13, color=T.TEXT_2, anchor="center", justify="center",
                wraplength=470).grid(row=2, column=0, padx=40, pady=(6, 20))

        tools = ctk.CTkFrame(card, fg_color=T.FIELD, corner_radius=12)
        tools.grid(row=3, column=0, sticky="ew", padx=32)
        tools.grid_columnconfigure(0, weight=1)
        for i, tool in enumerate(TOOLS):
            name, desc = TOOL_TEXT.get(tool, (tool, ""))
            row = ctk.CTkFrame(tools, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", padx=18,
                     pady=(16 if i == 0 else 8, 16 if i == len(TOOLS) - 1 else 8))
            row.grid_columnconfigure(0, weight=1)
            head = ctk.CTkFrame(row, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew")
            head.grid_columnconfigure(1, weight=1)
            T.label(head, name, 13, "semibold").grid(row=0, column=0, sticky="w")
            T.label(head, f"  {desc}", 12, color=T.TEXT_2).grid(row=0, column=1, sticky="w")
            chip = Chip(head, "Waiting", "neutral")
            chip.grid(row=0, column=2, sticky="e")
            bar = T.progress_bar(row, width=460)
            bar.set(0)
            bar.configure(progress_color=T.TRACK)
            bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
            msg = T.label(row, "", 11, color=T.TEXT_3, height=16)
            msg.grid(row=2, column=0, sticky="w", pady=(3, 0))
            self._rows[tool] = {"chip": chip, "bar": bar, "msg": msg, "indet": False,
                                "color": T.TRACK}

        self.status = T.label(card, "", 12, color=T.TEXT_2, anchor="center", justify="center",
                              wraplength=470)
        self.status.grid(row=4, column=0, padx=40, pady=(16, 0))
        btns = ctk.CTkFrame(card, fg_color="transparent", width=1, height=1)
        btns.grid(row=5, column=0, pady=(14, 8))
        self.retry_btn = T.accent_button(btns, "Retry", self.start, icon=T.Icon.REFRESH,
                                         width=120)
        self.skip_btn = T.ghost_button(btns, "Continue anyway", self._skip, height=38)
        T.label(card, f"Install location: {truncate(str(TOOLS_DIR), 70)}", 11, color=T.TEXT_3,
                anchor="center").grid(row=6, column=0, pady=(4, 26), padx=40)

    # ------------------------------------------------------------------ flow
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._current = None
        self.retry_btn.grid_forget()
        self.skip_btn.grid_forget()
        for w in self._rows.values():
            if w["chip"].cget("text") != "Installed":
                self._set_bar(w, 0)
                w["chip"].set("Waiting", "neutral")
                w["msg"].configure(text="")
        self.status.configure(text="Setting things up… this can take a minute on slower "
                                   "connections.", text_color=T.TEXT_2)
        threading.Thread(target=self._worker, daemon=True, name="setup").start()

    def _worker(self) -> None:
        deps = self.app.deps
        try:
            missing = set(deps.missing())
        except Exception:  # noqa: BLE001
            missing = set(TOOLS)
        self.app.post(self._mark_present, [t for t in TOOLS if t not in missing])
        try:
            deps.install_missing(progress=lambda t, m, f: self.app.post(self._progress, t, m, f))
        except DependencyError as exc:
            self.app.post(self._failed, friendly_error(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("install_missing failed")
            self.app.post(self._failed, f"Unexpected error: {friendly_error(exc)}")
            return
        ready = False
        try:
            ready = deps.is_ready()
        except Exception:  # noqa: BLE001
            pass
        if ready:
            self.app.post(self._succeeded)
        else:
            self.app.post(self._failed, "Some tools are still missing after setup.")

    def _mark_present(self, present: list[str]) -> None:
        for tool in present:
            w = self._rows.get(tool)
            if w:
                self._set_bar(w, 1.0)
                w["chip"].set("Installed", "success")
                w["msg"].configure(text="Already installed")

    def _set_bar(self, w: dict[str, Any], frac: float | None) -> None:
        bar = w["bar"]
        if frac is None:
            if not w["indet"]:
                w["indet"] = True
                bar.configure(mode="indeterminate")
                bar.start()
        else:
            if w["indet"]:
                w["indet"] = False
                bar.stop()
                bar.configure(mode="determinate")
            value = max(0.0, min(1.0, frac))
            bar.set(value)
        # An empty CTkProgressBar still draws a dot; hide it by matching the track color.
        color = T.TRACK if frac is not None and frac <= 0 else T.ACCENT
        if w.get("color") != color:
            w["color"] = color
            bar.configure(progress_color=color)

    def _progress(self, tool: str, message: str, frac: float | None) -> None:
        w = self._rows.get(tool)
        if w is None:
            self.status.configure(text=message)
            return
        prev = self._current
        if prev is not None and prev != tool and prev in self._rows:
            done = self._rows[prev]
            self._set_bar(done, 1.0)
            done["chip"].set("Installed", "success")
        self._current = tool
        self._set_bar(w, frac)
        w["msg"].configure(text=truncate(message, 80))
        w["chip"].set("Installing", "accent")

    def _failed(self, message: str) -> None:
        self.running = False
        for w in self._rows.values():
            if w["indet"]:
                self._set_bar(w, 0)
            if w["chip"].cget("text") == "Installing":
                w["chip"].set("Failed", "danger")
        self.status.configure(text=message, text_color=T.DANGER)
        self.retry_btn.grid(row=0, column=0, padx=(0, 8))
        self.skip_btn.grid(row=0, column=1)

    def _succeeded(self) -> None:
        self.running = False
        for w in self._rows.values():
            self._set_bar(w, 1.0)
            w["chip"].set("Installed", "success")
        self.status.configure(text="All set! Opening the app…", text_color=T.SUCCESS)
        self.after(900, self.app.finish_setup)

    def _skip(self) -> None:
        self.app.finish_setup(skipped=True)
