"""About & Updates page: credits/links, tool versions, update check and update-all."""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ui import (APP_NAME, APP_VERSION, AUTHOR, GITHUB_PROFILE_URL, ISSUES_URL, REPO_URL,
                WEBSITE_URL)
from ui import theme as T
from ui.backend import TOOLS, TOOLS_DIR, DependencyError
from ui.util import fmt_ago, friendly_error, open_folder, truncate
from ui.widgets import ScrollArea, Card, Chip, LinkLabel, PageHeader

if TYPE_CHECKING:
    from ui.app import App

log = logging.getLogger(__name__)

TOOL_INFO = {
    "yt-dlp": ("yt-dlp", "Download engine"),
    "ffmpeg": ("FFmpeg", "Merging, conversion & thumbnails"),
    "deno": ("Deno", "JavaScript runtime needed for YouTube"),
}


class AboutPage(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.status: dict[str, dict] = {}
        self.updates: dict[str, dict] = {}
        self.busy = False
        self._rows: dict[str, dict[str, Any]] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        PageHeader(self, "About & updates",
                   "App information and the download tools it relies on.").grid(
            row=0, column=0, sticky="ew", pady=(0, 18))
        body = ScrollArea(self)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        self._build_about(body).grid(row=0, column=0, sticky="ew", pady=(0, 14), padx=(0, 6))
        self._build_tools(body).grid(row=1, column=0, sticky="ew", pady=(0, 14), padx=(0, 6))

    # ------------------------------------------------------------------ about card
    def _build_about(self, parent: Any) -> Card:
        card = Card(parent)
        card.grid_columnconfigure(1, weight=1)
        self._logo = T.load_app_logo(64)
        ctk.CTkLabel(card, text="" if self._logo else T.Icon.DOWNLOAD, image=self._logo,
                     width=64, height=64, font=T.icon_font(32), text_color=T.ACCENT_TEXT).grid(
            row=0, column=0, rowspan=4, padx=(22, 18), pady=22, sticky="n")
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=1, sticky="w", pady=(22, 0))
        T.label(head, APP_NAME, 20, "semibold").grid(row=0, column=0, sticky="w")
        Chip(head, f"v{APP_VERSION}", "accent").grid(row=0, column=1, padx=(10, 0))
        by = ctk.CTkFrame(card, fg_color="transparent")
        by.grid(row=1, column=1, sticky="w", pady=(2, 0))
        T.label(by, "Created by", 13, color=T.TEXT_2).grid(row=0, column=0)
        LinkLabel(by, AUTHOR, WEBSITE_URL, 13, weight="semibold").grid(row=0, column=1,
                                                                        padx=(4, 0))
        T.label(card, "Free and open source. Saves video and audio from YouTube, Instagram, "
                      "TikTok, X, SoundCloud and many more sites.", 12, color=T.TEXT_2,
                wraplength=520).grid(row=2, column=1, sticky="w", pady=(4, 0), padx=(0, 20))

        links = ctk.CTkFrame(card, fg_color="transparent")
        links.grid(row=3, column=1, sticky="w", pady=(12, 22))
        for i, (glyph, text, url) in enumerate((
            (T.Icon.GLOBE, "Website", WEBSITE_URL),
            (T.Icon.PERSON, "GitHub profile", GITHUB_PROFILE_URL),
            (T.Icon.CODE, "Source code", REPO_URL),
            (T.Icon.BUG, "Report an issue", ISSUES_URL),
        )):
            item = ctk.CTkFrame(links, fg_color="transparent")
            item.grid(row=0, column=i, padx=(0, 20))
            T.glyph(item, glyph, 13, T.ACCENT_TEXT, width=16).grid(row=0, column=0, padx=(0, 5))
            LinkLabel(item, text, url, 13, weight="semibold").grid(row=0, column=1)
        return card

    # ------------------------------------------------------------------ tools card
    def _build_tools(self, parent: Any) -> Card:
        card = Card(parent)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        T.glyph(title, T.Icon.TOOLS, 15, T.ACCENT_TEXT, width=20).grid(row=0, column=0,
                                                                     padx=(0, 8))
        T.label(title, "Download tools", 15, "semibold").grid(row=0, column=1)
        self.checked_label = T.label(head, "", 12, color=T.TEXT_3)
        self.checked_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, rowspan=2, sticky="e")
        self.check_btn = T.ghost_button(btns, "Check for updates", self.check_updates,
                                        icon=T.Icon.REFRESH, height=36)
        self.check_btn.grid(row=0, column=0)
        self.update_btn = T.accent_button(btns, "Update all", self.update_all, icon=T.Icon.SYNC,
                                          height=36, size=12)
        self.update_btn.grid(row=0, column=1, padx=(8, 0))

        table = ctk.CTkFrame(card, fg_color="transparent")
        table.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 0))
        widths = (0, 150, 150, 150)
        table.grid_columnconfigure(0, weight=1)
        headers = ("Tool", "Installed", "Latest", "Status")
        for c, (text, w) in enumerate(zip(headers, widths, strict=True)):
            if w:
                table.grid_columnconfigure(c, minsize=w)
            T.label(table, text.upper(), 11, "semibold", T.TEXT_3).grid(row=0, column=c,
                                                                       sticky="w", pady=(0, 6))
        for r, tool in enumerate(TOOLS, start=1):
            name, desc = TOOL_INFO.get(tool, (tool, ""))
            sep = ctk.CTkFrame(table, height=1, corner_radius=0, fg_color=T.CARD_BORDER)
            sep.grid(row=r * 2 - 1, column=0, columnspan=4, sticky="ew")
            cell = ctk.CTkFrame(table, fg_color="transparent")
            cell.grid(row=r * 2, column=0, sticky="w", pady=10)
            T.label(cell, name, 13, "semibold").grid(row=0, column=0, sticky="w")
            T.label(cell, desc, 12, color=T.TEXT_2).grid(row=1, column=0, sticky="w")
            cur = T.label(table, "—", 13, color=T.TEXT_2)
            cur.grid(row=r * 2, column=1, sticky="w")
            latest = T.label(table, "—", 13, color=T.TEXT_2)
            latest.grid(row=r * 2, column=2, sticky="w")
            chip = Chip(table, "Checking…", "neutral")
            chip.grid(row=r * 2, column=3, sticky="w")
            self._rows[tool] = {"current": cur, "latest": latest, "chip": chip}

        prog = ctk.CTkFrame(card, fg_color="transparent")
        prog.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 0))
        prog.grid_columnconfigure(0, weight=1)
        self.progress = T.progress_bar(prog)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_text = T.label(prog, "", 12, color=T.TEXT_2)
        self.progress_text.grid(row=1, column=0, sticky="w", pady=(6, 0))
        prog.grid_remove()
        self._prog_frame = prog

        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 18))
        foot.grid_columnconfigure(1, weight=1)
        T.glyph(foot, T.Icon.FOLDER, 13, T.TEXT_3, width=18).grid(row=0, column=0, padx=(0, 6))
        T.label(foot, truncate(str(TOOLS_DIR), 80), 12, color=T.TEXT_3).grid(row=0, column=1,
                                                                          sticky="w")
        LinkLabel(foot, "Open tools folder", command=self._open_tools, size=12).grid(
            row=0, column=2)
        self._update_checked_label()
        return card

    # ------------------------------------------------------------------ data -> view
    def set_status(self, status: dict[str, dict]) -> None:
        self.status = status
        # Reconcile a cached update check with what is installed now (e.g. after `--update`).
        for tool, up in self.updates.items():
            version = status.get(tool, {}).get("version")
            if version and up.get("latest") and str(version) == str(up["latest"]):
                up["update_available"] = False
                up["current"] = version
        self._render()

    def set_updates(self, updates: dict[str, dict]) -> None:
        self.updates = updates
        self._render()
        self._update_checked_label()

    def _render(self) -> None:
        for tool, w in self._rows.items():
            st = self.status.get(tool, {})
            up = self.updates.get(tool, {})
            installed = st.get("installed", up.get("current") is not None) if (st or up) else None
            current = st.get("version") or up.get("current")
            latest = up.get("latest")
            w["current"].configure(text=truncate(str(current), 22) if current else "—")
            w["latest"].configure(text=truncate(str(latest), 22) if latest else "—")
            if installed is None:
                w["chip"].set("Checking…", "neutral")
            elif not installed:
                w["chip"].set("Not installed", "danger")
            elif up.get("update_available"):
                w["chip"].set("Update available", "warning")
            elif up:
                w["chip"].set("Up to date" if latest else "Installed", "success" if latest
                              else "neutral")
            else:
                w["chip"].set("Installed", "neutral")

    def _update_checked_label(self) -> None:
        last = float(self.app.cfg.get("last_update_check") or 0)
        self.checked_label.configure(text=f"Last checked {fmt_ago(last)}")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.check_btn.configure(state=state)
        self.update_btn.configure(state=state)
        if busy:
            self._prog_frame.grid()
            self.progress_text.configure(text=text)
            self._indeterminate(True)
        else:
            self._indeterminate(False)
            self._prog_frame.grid_remove()

    def _indeterminate(self, on: bool) -> None:
        if on:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")

    # ------------------------------------------------------------------ actions
    def refresh_status(self) -> None:
        """Re-read installed versions in the background (no network)."""
        def work() -> None:
            try:
                st = self.app.deps.status()
            except Exception as exc:  # noqa: BLE001
                log.warning("status failed: %s", exc)
                return
            self.app.post(self._status_done, st)
        threading.Thread(target=work, daemon=True, name="tool-status").start()

    def _status_done(self, st: dict[str, dict]) -> None:
        self.set_status(st)
        self.app.refresh_tools_indicator()

    def check_updates(self, silent: bool = False) -> None:
        if self.busy:
            return
        if not silent:
            self._set_busy(True, "Checking for updates…")
        self.app.set_tools_indicator("checking")

        def work() -> None:
            try:
                res = self.app.deps.check_updates()
            except Exception as exc:  # noqa: BLE001
                self.app.post(self._check_failed, friendly_error(exc), silent)
                return
            self.app.post(self._check_done, res, silent)
        threading.Thread(target=work, daemon=True, name="check-updates").start()

    def _check_done(self, res: dict[str, dict], silent: bool) -> None:
        if not silent:
            self._set_busy(False)
        self.app.set_config(last_update_check=time.time(), last_update_info=res)
        self.set_updates(res)
        self.app.refresh_tools_indicator()
        n = sum(1 for v in res.values() if v.get("update_available"))
        unknown = all(v.get("latest") is None for v in res.values())
        if silent:
            if n:
                self.app.toast(f"{n} tool update{'s' if n > 1 else ''} available.", "info",
                               "View", lambda: self.app.show_page("about"))
        elif unknown:
            self.app.toast("Couldn't reach GitHub to check for updates. Try again later.",
                           "warning")
        elif n:
            self.app.toast(f"{n} update{'s' if n > 1 else ''} available.", "info",
                           "Update all", self.update_all)
        else:
            self.app.toast("Everything is up to date.", "success")

    def _check_failed(self, message: str, silent: bool) -> None:
        if not silent:
            self._set_busy(False)
            self.app.toast(f"Update check failed: {message}", "error")
        self.app.refresh_tools_indicator()

    def update_all(self) -> None:
        if self.busy:
            return
        if self.app.queue_page.pending_count():
            self.app.toast("Wait for the running downloads to finish before updating tools.",
                           "warning", "View queue", lambda: self.app.show_page("queue"))
            return
        self._set_busy(True, "Preparing…")
        self.app.set_tools_indicator("updating")
        self.app.updating_tools = True

        def progress(tool: str, message: str, frac: float | None) -> None:
            self.app.post(self._on_progress, tool, message, frac)

        def work() -> None:
            deps = self.app.deps
            try:
                if not deps.is_ready():
                    deps.install_missing(progress=progress)
                results = deps.update_all(progress=progress)
                status = deps.status()
                updates = deps.check_updates()
            except DependencyError as exc:
                self.app.post(self._update_failed, friendly_error(exc))
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("update_all failed")
                self.app.post(self._update_failed, friendly_error(exc))
                return
            self.app.post(self._update_done, results, status, updates)
        threading.Thread(target=work, daemon=True, name="update-all").start()

    def _on_progress(self, tool: str, message: str, frac: float | None) -> None:
        name = TOOL_INFO.get(tool, (tool, ""))[0]
        self.progress_text.configure(text=f"{name}: {message}" if message else name)
        if frac is None:
            if self.progress.cget("mode") != "indeterminate":
                self._indeterminate(True)
        else:
            if self.progress.cget("mode") != "determinate":
                self._indeterminate(False)
            self.progress.set(max(0.0, min(1.0, frac)))

    def _update_done(self, results: dict[str, str], status: dict, updates: dict) -> None:
        self.app.updating_tools = False
        self._set_busy(False)
        self.app.set_config(last_update_check=time.time(), last_update_info=updates)
        self.set_status(status)
        self.set_updates(updates)
        self.app.refresh_tools_indicator()
        failed = {t: r for t, r in results.items() if str(r).lower().startswith("failed")}
        updated = [TOOL_INFO.get(t, (t,))[0] for t, r in results.items()
                   if str(r).lower().startswith("updated")]
        if failed:
            t, r = next(iter(failed.items()))
            self.app.toast(f"{TOOL_INFO.get(t, (t,))[0]} could not be updated: "
                           f"{truncate(str(r)[7:].strip(' :'), 160)}", "error")
        elif updated:
            self.app.toast(f"Updated {', '.join(updated)}.", "success")
        else:
            self.app.toast("All tools are already up to date.", "success")

    def _update_failed(self, message: str) -> None:
        self.app.updating_tools = False
        self._set_busy(False)
        self.app.refresh_tools_indicator()
        self.app.toast(f"Update failed: {message}", "error")
        self.refresh_status()

    def _open_tools(self) -> None:
        try:
            open_folder(str(TOOLS_DIR))
        except OSError as exc:
            self.app.toast(f"Couldn't open the folder: {friendly_error(exc)}", "error")
