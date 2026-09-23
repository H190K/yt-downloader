"""Queue page: job rows plus the scheduler that runs at most ``MAX_CONCURRENT`` downloads."""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ui import theme as T
from ui.backend import DownloadJob, JobOptions
from ui.util import (BITRATE_LABELS, friendly_error, open_folder,
                     quality_label, show_in_explorer, truncate)
from ui.widgets import Card, Chip, PageHeader, ScrollArea, fit_text

if TYPE_CHECKING:
    from ui.app import App

log = logging.getLogger(__name__)

MAX_CONCURRENT = 2
PROGRESS_MS = 100  # UI refresh interval for download progress (~10 updates/s)
ACTIVE = ("starting", "downloading", "processing", "cancelling")
FINISHED = ("done", "error", "cancelled")

_STATE_STYLE: dict[str, tuple[str, str]] = {
    "queued": ("Waiting", "neutral"),
    "starting": ("Starting", "accent"),
    "downloading": ("Downloading", "accent"),
    "processing": ("Processing", "accent"),
    "cancelling": ("Cancelling", "neutral"),
    "done": ("Completed", "success"),
    "error": ("Failed", "danger"),
    "cancelled": ("Cancelled", "neutral"),
}


def describe_options(opts: JobOptions) -> str:
    kind = opts.kind.upper()
    if opts.kind == "mp4":
        q = "Best quality" if opts.quality == "best" else quality_label(opts.quality)
        text = f"{kind} · {q}"
    elif opts.kind == "mp3":
        text = f"{kind} · {BITRATE_LABELS.get(opts.mp3_bitrate, opts.mp3_bitrate)}"
    else:
        text = f"{kind} · Best quality"
    return text + (" · Playlist" if opts.playlist else "")


class JobRow(Card):
    """One queue entry. ``state`` here is the UI's view; the backend job keeps its own."""

    def __init__(self, master: Any, page: "QueuePage", job: DownloadJob, has_video: bool) -> None:
        super().__init__(master, corner_radius=12)
        self.page = page
        self.job = job
        self.state = "queued"
        self.cancel_requested = False
        self.has_video = has_video
        self._last_fraction: float | None = 0.0
        self._indeterminate = False
        self._state_applied = False
        self._fit_job: str | None = None

        self.grid_columnconfigure(1, weight=1)
        box = ctk.CTkFrame(self, width=44, height=44, corner_radius=10, fg_color=T.ACCENT_SOFT)
        box.grid(row=0, column=0, rowspan=3, padx=(16, 14), pady=16, sticky="n")
        box.grid_propagate(False)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self.icon = T.glyph(box, T.Icon.VIDEO if has_video else T.Icon.MUSIC, 18, T.ACCENT_TEXT)
        self.icon.grid(row=0, column=0)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=1, sticky="ew", pady=(14, 0))
        top.grid_columnconfigure(0, weight=1)
        top.bind("<Configure>", lambda _e: self._schedule_fit(), add="+")
        self._top = top
        self._title_text = job.title
        self.title = T.label(top, truncate(job.title, 40), 13, "semibold")
        self.title.grid(row=0, column=0, sticky="w")
        self.state_chip = Chip(top, "Waiting", "neutral")
        self.state_chip.grid(row=0, column=1, sticky="e", padx=(10, 0))

        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        meta.grid_columnconfigure(1, weight=1)
        self.opts_label = T.label(meta, describe_options(job.options), 12, color=T.TEXT_2)
        self.opts_label.grid(row=0, column=0, sticky="w")
        self.detail = T.label(meta, "Waiting for a free slot…", 12, color=T.TEXT_3,
                              anchor="e", justify="right")
        self.detail.grid(row=0, column=1, sticky="e", padx=(10, 0))

        self.bar = T.progress_bar(self, mode="determinate")
        self.bar.set(0)
        self.bar.grid(row=2, column=1, sticky="ew", pady=(10, 16))

        self.error_label = T.label(self, "", 12, color=T.DANGER, wraplength=560)

        self.buttons = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons.grid(row=0, column=2, rowspan=3, padx=(14, 14), sticky="e")
        # Buttons are created lazily: most rows only ever need one or two of them, and every
        # CTk widget costs a redraw on resize / theme switch.
        self._btns: dict[str, ctk.CTkButton] = {}
        self._btn_layout: tuple[str, ...] = ()
        self.bind("<Configure>", self._on_resize, add="+")
        self.set_state("queued")

    # ------------------------------------------------------------------ display
    def _on_resize(self, event: Any) -> None:
        scaling = self._get_widget_scaling() or 1.0
        wrap = max(200, int(event.width / scaling) - 260)
        if wrap != self.error_label.cget("wraplength"):
            self.error_label.configure(wraplength=wrap)

    def _schedule_fit(self) -> None:
        """Debounced title fitting (resizes fire many <Configure> events)."""
        if self._fit_job is None:
            self._fit_job = self.after(60, self._run_fit)

    def _run_fit(self) -> None:
        self._fit_job = None
        self._fit_title()

    def _fit_title(self) -> None:
        width = self._top.winfo_width()
        if width <= 1:
            return
        scaling = self._get_widget_scaling() or 1.0
        avail = (width - self.state_chip.winfo_reqwidth()) / scaling - 20
        text = fit_text(self._title_text, T.font(13, "semibold"), max(60, int(avail)))
        if text != self.title.cget("text"):
            self.title.configure(text=text)

    def _button(self, name: str) -> ctk.CTkButton:
        btn = self._btns.get(name)
        if btn is None:
            b = self.buttons
            btn = {
                "cancel": lambda: T.ghost_button(b, "Cancel", self.cancel, icon=T.Icon.CLOSE,
                                                 width=92, height=32, danger=True),
                "show": lambda: T.accent_button(b, "Show file", self._show_file,
                                                icon=T.Icon.SHOW, width=110, height=32, size=12),
                "folder": lambda: T.icon_button(b, T.Icon.FOLDER, self._open_folder, size=32),
                "retry": lambda: T.ghost_button(b, "Retry", self.retry, icon=T.Icon.REFRESH,
                                                width=88, height=32),
                "remove": lambda: T.icon_button(b, T.Icon.DELETE, self.remove, size=32),
            }[name]()
            self._btns[name] = btn
        return btn

    def _layout_buttons(self) -> None:
        if self.state in ("queued",) + ACTIVE:
            layout: tuple[str, ...] = ("cancel",)
        elif self.state == "done":
            layout = ("show", "folder", "remove")
        else:
            layout = ("retry", "remove")
        if layout != self._btn_layout:
            for btn in self._btns.values():
                btn.grid_forget()
            for i, name in enumerate(layout):
                self._button(name).grid(row=0, column=i, padx=(0 if i == 0 else 4, 0))
            self._btn_layout = layout
        if "cancel" in layout:
            want = "disabled" if self.state == "cancelling" else "normal"
            btn = self._btns["cancel"]
            if btn.cget("state") != want:
                btn.configure(state=want)

    def set_state(self, state: str, detail: str | None = None) -> None:
        if state == self.state and self._state_applied:
            if detail is not None:
                self._set_detail(detail)
            return
        self._state_applied = True
        self.state = state
        text, style = _STATE_STYLE.get(state, (state.title(), "neutral"))
        self.state_chip.set(text, style)
        if detail is not None:
            self._set_detail(detail)
        if state in ("processing", "starting", "cancelling"):
            self._set_indeterminate(True)
        else:
            self._set_indeterminate(False)
        if state == "done":
            self.bar.configure(progress_color=T.SUCCESS)
            self.bar.set(1)
        elif state == "error":
            self.bar.configure(progress_color=T.DANGER)
        elif state == "cancelled":
            self.bar.configure(progress_color=T.TEXT_3)
        elif state == "queued":
            self.bar.configure(progress_color=T.TRACK)
        else:
            self.bar.configure(progress_color=T.ACCENT)
        if state != "error":
            self.error_label.grid_forget()
        self._layout_buttons()
        self._schedule_fit()

    def _set_indeterminate(self, on: bool) -> None:
        if on == self._indeterminate:
            return
        self._indeterminate = on
        if on:
            self.bar.configure(mode="indeterminate")
            self.bar.start()
        else:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar.set(self._last_fraction or 0)

    def apply_progress(self, d: dict) -> None:
        if self.state in FINISHED or self.state == "cancelling":
            return
        title = getattr(self.job, "title", None)
        if title and title != self._title_text:
            self._title_text = title
            self._fit_title()
        status = d.get("status") or "downloading"
        frac = d.get("fraction")
        parts: list[str] = []
        item = d.get("item")
        if item:
            parts.append(f"Item {item}")
        if status == "processing":
            parts.append(d.get("message") or "Finishing up…")
            self.set_state("processing", "  ·  ".join(parts))
            return
        if d.get("percent"):
            parts.append(str(d["percent"]).strip())
        if d.get("speed"):
            parts.append(str(d["speed"]).strip())
        if d.get("eta"):
            parts.append(f"ETA {str(d['eta']).strip()}")
        if not parts and d.get("message"):
            parts.append(str(d["message"]))
        if self.state != "downloading":
            self.set_state("downloading")
        if frac is None:
            self._set_indeterminate(True)
        else:
            self._last_fraction = max(0.0, min(1.0, float(frac)))
            self._set_indeterminate(False)
            if abs(self.bar.get() - self._last_fraction) > 0.0005:
                self.bar.set(self._last_fraction)
        self._set_detail("  ·  ".join(parts))

    def _set_detail(self, text: str) -> None:
        if text != self.detail.cget("text"):
            self.detail.configure(text=text)

    def finish(self, ok: bool, message: str) -> None:
        job_state = getattr(self.job, "state", "")
        if ok:
            out = self.job.output_path or ""
            name = os.path.basename(out.rstrip("/\\")) if out else ""
            if message and message.lower().startswith("finished with errors"):
                detail = message.split(". Saved to")[0]
            else:
                detail = name or "Saved to your download folder"
            self.set_state("done", truncate(detail, 70))
        elif self.cancel_requested or job_state == "cancelled":
            self.set_state("cancelled", "Cancelled")
        else:
            self.set_state("error", "")
            self.error_label.configure(text=message or "The download failed.")
            self.error_label.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 14),
                                  padx=(0, 14))
            self.bar.grid_configure(pady=(10, 8))
            return
        self.bar.grid_configure(pady=(10, 16))

    # ------------------------------------------------------------------ actions
    def cancel(self) -> None:
        self.page.cancel(self)

    def retry(self) -> None:
        self.page.retry(self)

    def remove(self) -> None:
        self.page.remove(self)

    def _show_file(self) -> None:
        path = self.job.output_path
        if path and os.path.exists(path):
            show_in_explorer(path)
        else:
            self._open_folder()

    def _open_folder(self) -> None:
        path = self.job.output_path
        if path and os.path.isdir(path):
            folder = path
        elif path and os.path.isfile(path):
            folder = os.path.dirname(path)
        else:
            folder = self.job.options.out_dir
        try:
            open_folder(folder)
        except OSError as exc:
            self.page.app.toast(f"Couldn't open the folder: {friendly_error(exc)}", "error")


class QueuePage(ctk.CTkFrame):
    def __init__(self, master: Any, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.rows: list[JobRow] = []
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.header = PageHeader(self, "Queue", "")
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.clear_btn = T.ghost_button(self.header.actions, "Clear finished", self.clear_finished,
                                        icon=T.Icon.DELETE)
        self.clear_btn.grid(row=0, column=1, padx=(8, 0))
        T.ghost_button(self.header.actions, "Open folder",
                       lambda: self._open_dir(), icon=T.Icon.FOLDER).grid(row=0, column=0)

        self.list = ScrollArea(self)
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)

        self.empty = Card(self)
        self.empty.grid_columnconfigure(0, weight=1)
        T.glyph(self.empty, T.Icon.QUEUE, 30, T.TEXT_3).grid(row=0, column=0, pady=(40, 8))
        T.label(self.empty, "Nothing in the queue", 15, "semibold", anchor="center").grid(
            row=1, column=0)
        T.label(self.empty, "Downloads you start will appear here with their progress.\n"
                            "Up to two run at the same time; the rest wait their turn.",
                12, color=T.TEXT_2, anchor="center", justify="center").grid(row=2, column=0,
                                                                           pady=(4, 16))
        T.accent_button(self.empty, "Go to Download", lambda: self.app.show_page("download"),
                        icon=T.Icon.DOWNLOAD).grid(row=3, column=0, pady=(0, 40))
        self._refresh_summary()
        self._pump_job: str | None = self.after(PROGRESS_MS, self._progress_pump)

    def destroy(self) -> None:
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except Exception:  # noqa: BLE001
                pass
            self._pump_job = None
        super().destroy()

    # ------------------------------------------------------------------ public
    def add(self, url: str, title: str, options: JobOptions, has_video: bool = True) -> JobRow:
        job = DownloadJob(url, title, options, self.app.deps)
        row = JobRow(self.list, self, job, has_video)
        self.rows.append(row)
        self._regrid()
        self._pump()
        return row

    def active_count(self) -> int:
        return sum(1 for r in self.rows if r.state in ACTIVE)

    def pending_count(self) -> int:
        return sum(1 for r in self.rows if r.state == "queued" or r.state in ACTIVE)

    def cancel_all(self) -> None:
        for row in list(self.rows):
            if row.state == "queued" or row.state in ACTIVE:
                self.cancel(row, quiet=True)

    # ------------------------------------------------------------------ scheduling
    def _pump(self) -> None:
        running = self.active_count()
        for row in self.rows:
            if running >= MAX_CONCURRENT:
                break
            if row.state == "queued":
                self._start(row)
                running += 1
        self._refresh_summary()

    def _start(self, row: JobRow) -> None:
        row.set_state("starting", "Starting…")
        job = row.job
        try:
            job.start(on_progress=self._on_progress, on_done=self._on_done)
        except Exception as exc:  # noqa: BLE001
            log.exception("job.start failed")
            row.finish(False, friendly_error(exc))

    def _on_progress(self, job: DownloadJob, d: dict) -> None:
        """Worker thread: just remember the latest update; the UI pump applies it."""
        with self._lock:
            self._pending[job.id] = dict(d)

    def _progress_pump(self) -> None:
        """Apply pending progress at most ``1000 / PROGRESS_MS`` times per second per job."""
        with self._lock:
            pending, self._pending = self._pending, {}
        for job_id, d in pending.items():
            row = self._row_for(job_id)
            if row is not None:
                row.apply_progress(d)
        self._pump_job = self.after(PROGRESS_MS, self._progress_pump)

    def _on_done(self, job: DownloadJob, ok: bool, message: str) -> None:
        self.app.post(self._finish, job.id, ok, message)

    def _finish(self, job_id: str, ok: bool, message: str) -> None:
        with self._lock:
            self._pending.pop(job_id, None)
        row = self._row_for(job_id)
        if row is None:
            return
        row.finish(ok, message)
        if row.state == "done" and self.app.current_page != "queue":
            self.app.toast(f"Downloaded: {truncate(row.job.title, 60)}", "success", "Show file",
                           row._show_file)
        elif row.state == "error":
            self.app.toast(f"Download failed: {truncate(message, 140)}", "error", "View queue",
                           lambda: self.app.show_page("queue"))
        self._pump()

    def _row_for(self, job_id: str) -> JobRow | None:
        return next((r for r in self.rows if r.job.id == job_id), None)

    # ------------------------------------------------------------------ row actions
    def cancel(self, row: JobRow, quiet: bool = False) -> None:
        row.cancel_requested = True
        if row.state == "queued":
            row.set_state("cancelled", "Cancelled")
            try:
                row.job.cancel()
            except Exception:  # noqa: BLE001 - job never started; nothing to clean up
                pass
            self._pump()
            return
        if row.state in FINISHED or row.state == "cancelling":
            return
        row.set_state("cancelling", "Stopping…")
        threading.Thread(target=self._cancel_worker, args=(row.job,), daemon=True,
                         name="cancel-job").start()
        self._refresh_summary()

    def _cancel_worker(self, job: DownloadJob) -> None:
        try:
            job.cancel()  # may block while the process tree is killed
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel failed: %s", exc)

    def retry(self, row: JobRow) -> None:
        old = row.job
        row.job = DownloadJob(old.url, old.title, old.options, self.app.deps)
        row.cancel_requested = False
        row._last_fraction = 0.0
        row.bar.set(0)
        row.bar.grid_configure(pady=(10, 16))
        row.error_label.grid_forget()
        row.set_state("queued", "Waiting for a free slot…")
        self._pump()

    def remove(self, row: JobRow) -> None:
        if row.state not in FINISHED:
            return
        self.rows.remove(row)
        row.destroy()
        self._regrid()
        self._refresh_summary()

    def clear_finished(self) -> None:
        for row in [r for r in self.rows if r.state in FINISHED]:
            self.rows.remove(row)
            row.destroy()
        self._regrid()
        self._refresh_summary()

    def _open_dir(self) -> None:
        try:
            open_folder(str(self.app.cfg.get("download_dir")))
        except OSError as exc:
            self.app.toast(f"Couldn't open the folder: {friendly_error(exc)}", "error")

    # ------------------------------------------------------------------ layout
    def _regrid(self) -> None:
        for i, row in enumerate(self.rows):
            row.grid(row=i, column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        if self.rows:
            self.empty.grid_forget()
            self.list.grid(row=1, column=0, sticky="nsew")
        else:
            self.list.grid_forget()
            self.empty.grid(row=1, column=0, sticky="new")

    def _refresh_summary(self) -> None:
        active = self.active_count()
        waiting = sum(1 for r in self.rows if r.state == "queued")
        done = sum(1 for r in self.rows if r.state == "done")
        failed = sum(1 for r in self.rows if r.state == "error")
        if not self.rows:
            text = "Your downloads will show up here."
        else:
            parts = [f"{active} downloading", f"{waiting} waiting", f"{done} completed"]
            if failed:
                parts.append(f"{failed} failed")
            text = "  ·  ".join(parts)
        self.header.subtitle.configure(text=text)
        self.clear_btn.configure(state="normal" if any(r.state in FINISHED for r in self.rows)
                                 else "disabled")
        self.app.set_queue_badge(active + waiting)
        if not self.rows:
            self._regrid()
