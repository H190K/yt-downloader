"""Queue page: job rows plus the scheduler that runs at most ``MAX_CONCURRENT`` downloads."""
from __future__ import annotations

import copy
import logging
import os
import re
import threading
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ui import theme as T
from ui.backend import DownloadJob, JobOptions
from ui.util import (BITRATE_LABELS, delivered_quality_text, friendly_error, media_key, middle_ellipsis,
                     open_folder,
                     quality_shortfall, requested_quality_text, short_quality,
                     show_in_explorer, truncate)
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
    """Requested-quality chip text, e.g. "MP4 · 720p" or "MP3 · 320 kbps · Playlist"."""
    text = requested_quality_text(opts.kind, opts.quality, opts.mp3_bitrate)
    return text + (" · Playlist" if opts.playlist else "")


def job_signature(url: str, opts: JobOptions) -> tuple[str, str, str, bool]:
    """Identity of a download for duplicate detection: same media + same output format.

    Only the option that matters for the kind is compared, so two MP4 jobs of one video at
    720p and 1080p are different, while re-queueing the identical request is a duplicate.
    """
    if opts.kind == "mp4":
        variant = str(opts.quality or "best")
    elif opts.kind == "mp3":
        variant = str(opts.mp3_bitrate)
    else:
        variant = "best"
    return media_key(url), opts.kind, variant, bool(opts.playlist)


class DuplicateJobError(Exception):
    """The identical download is already waiting or running."""

    def __init__(self, row: "JobRow") -> None:
        super().__init__("Already in the queue")
        self.row = row


#: ``job.error_kind`` values that are fixed by signing in (cookies from a browser, Settings).
LOGIN_ERROR_KINDS = frozenset({"login_required", "cookies_required", "age_restricted",
                               "members_only", "private", "private_video", "sign_in"})
_COLLAPSE_LINES = 4
_COLLAPSE_CHARS = 360


def _collapse_message(text: str) -> str | None:
    """Short preview of a long message, or None when it's short enough to show in full."""
    lines = text.splitlines()
    if len(lines) <= _COLLAPSE_LINES and len(text) <= _COLLAPSE_CHARS:
        return None
    head = "\n".join(lines[:3])
    if len(head) > 240:
        head = head[:240].rsplit(" ", 1)[0]
    return head.rstrip(" .,;:") + "…"


def _looks_like_path(text: str) -> bool:
    return bool(text) and (os.path.isabs(text) or os.path.exists(text))


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
        self._finished_job: str | None = None  # job id whose on_done was already applied
        self._message_color: Any = None
        self._detail_file: str | None = None  # finished file name, fitted with a middle "…"
        self._msg_full = ""
        self._msg_collapsed: str | None = None
        self._msg_expanded = False
        self._try_quality: str | None = None
        self._want_settings = False
        self._actions: ctk.CTkFrame | None = None  # built lazily (only failed/noted rows)
        self._action_widgets: dict[str, ctk.CTkButton] = {}

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
        meta.grid_columnconfigure(2, weight=1)
        self._meta = meta
        self.opts_chip = Chip(meta, describe_options(job.options), "neutral")
        self.opts_chip.grid(row=0, column=0, sticky="w")
        self.saved_chip = Chip(meta, "", "success")  # gridded once the job is done
        self.detail = T.label(meta, "Waiting for a free slot…", 12, color=T.TEXT_3,
                              anchor="e", justify="right")
        self.detail.grid(row=0, column=2, sticky="e", padx=(10, 0))

        self.bar = T.progress_bar(self, mode="determinate")
        self.bar.set(0)
        self.bar.grid(row=2, column=1, sticky="ew", pady=(10, 16))

        # Failure reason (danger) or a note on a successful download (warning / secondary).
        # Multi-line: the engine's friendly messages are 2-4 lines and are shown in full.
        self.message_label = T.label(self, "", 12, color=T.DANGER, wraplength=560,
                                     justify="left")

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
        if wrap != self.message_label.cget("wraplength"):
            self.message_label.configure(wraplength=wrap)

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
        self._fit_detail()

    def _fit_detail(self) -> None:
        """Fit the saved file name beside the chips, keeping its end (" - 720p.mp4") visible."""
        name = self._detail_file
        if not name:
            return
        width = self._meta.winfo_width()
        if width <= 1:
            self._set_detail(truncate(name, 60))
            return
        scaling = self._get_widget_scaling() or 1.0
        used = self.opts_chip.winfo_reqwidth()
        if self.saved_chip.winfo_manager():
            used += self.saved_chip.winfo_reqwidth()
        avail = (width - used) / scaling - 30
        self._set_detail(middle_ellipsis(name, T.font(12).measure, max(60, int(avail))))

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
        if state != "done":
            self._detail_file = None
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
        if state not in FINISHED:
            self._hide_message()
            self.saved_chip.grid_forget()
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
        # A late update (after on_done, or from a job replaced by Retry) must never flip a
        # finished row back to "downloading".
        if (self.state in FINISHED or self.state == "cancelling"
                or self._finished_job == self.job.id):
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

    # ------------------------------------------------------------------ message + actions
    def _show_message(self, text: str, color: Any, *, try_quality: str | None = None,
                      settings: bool = False) -> None:
        """Show the failure reason / success note under the bar, with optional actions.

        Long messages are collapsed to a few lines with a "Show more" toggle; short ones
        (the usual 2-4 line friendly message) are shown in full, wrapped.
        """
        self._msg_full = text
        self._msg_collapsed = _collapse_message(text)
        self._msg_expanded = False
        self._try_quality = try_quality
        self._want_settings = settings
        if color is not self._message_color:
            self._message_color = color
            self.message_label.configure(text_color=color)
        self._render_message()

    def _render_message(self) -> None:
        collapsed = self._msg_collapsed
        text = self._msg_full if (self._msg_expanded or collapsed is None) else collapsed
        if text != self.message_label.cget("text"):
            self.message_label.configure(text=text)
        has_actions = bool(self._try_quality or self._want_settings or collapsed is not None)
        self.message_label.grid(row=3, column=1, columnspan=2, sticky="w",
                                pady=(0, 6 if has_actions else 14), padx=(0, 14))
        self.bar.grid_configure(pady=(10, 8))
        if has_actions:
            self._layout_actions(collapsed is not None)
        elif self._actions is not None:
            self._actions.grid_forget()

    def _layout_actions(self, toggle: bool) -> None:
        if self._actions is None:
            self._actions = ctk.CTkFrame(self, fg_color="transparent")
        frame = self._actions
        wanted: list[str] = []
        if self._try_quality:
            wanted.append("try")
        if self._want_settings:
            wanted.append("settings")
        if toggle:
            wanted.append("more")
        for name, widget in self._action_widgets.items():
            if name not in wanted:
                widget.grid_forget()
        for i, name in enumerate(wanted):
            widget = self._action_widgets.get(name)
            if widget is None:
                widget = {
                    "try": lambda: T.accent_button(frame, "Try", self._try_suggested,
                                                   icon=T.Icon.REFRESH, height=30, size=12),
                    "settings": lambda: T.ghost_button(frame, "Open Settings",
                                                       lambda: self.page.app.show_page("settings"),
                                                       icon=T.Icon.SETTINGS, height=30),
                    "more": lambda: T.ghost_button(frame, "Show more", self._toggle_message,
                                                   height=30),
                }[name]()
                self._action_widgets[name] = widget
            widget.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0))
        if self._try_quality:
            label = f"Try {short_quality(self._try_quality)}"
            if self._action_widgets["try"].cget("text") != label:
                self._action_widgets["try"].configure(text=label)
        if toggle:
            label = "Show less" if self._msg_expanded else "Show more"
            if self._action_widgets["more"].cget("text") != label:
                self._action_widgets["more"].configure(text=label)
        frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=(0, 14))

    def _toggle_message(self) -> None:
        self._msg_expanded = not self._msg_expanded
        self._render_message()

    def _try_suggested(self) -> None:
        if self._try_quality:
            self.page.retry(self, quality=self._try_quality)

    def _hide_message(self) -> None:
        if self.message_label.winfo_manager():
            self.message_label.grid_forget()
            self.bar.grid_configure(pady=(10, 16))
        if self._actions is not None and self._actions.winfo_manager():
            self._actions.grid_forget()
        self._try_quality = None
        self._want_settings = False

    def suggested_quality(self) -> str | None:
        """A quality the engine says will work (``job.suggested_quality``), if it's new."""
        raw = getattr(self.job, "suggested_quality", None)
        if not raw:
            return None
        opts = self.job.options
        value = str(raw).strip().lower().removesuffix("kbps").removesuffix("p").strip()
        if opts.kind == "mp4":
            ok = value == "best" or value.isdigit()
            current = str(opts.quality)
        elif opts.kind == "mp3":
            ok = value in BITRATE_LABELS
            current = str(opts.mp3_bitrate)
        else:
            return None
        return value if ok and value != current else None

    def needs_login(self) -> bool:
        kind = str(getattr(self.job, "error_kind", "") or "").lower()
        return kind in LOGIN_ERROR_KINDS or any(w in kind for w in ("login", "cookie", "sign"))

    def delivered_note(self) -> str | None:
        """Explanation when the saved quality differs from the requested one, else None."""
        opts = self.job.options
        delivered = getattr(self.job, "delivered_quality", None)
        if not delivered or not quality_shortfall(opts.kind, opts.quality, str(delivered)):
            return None
        got = delivered_quality_text(str(delivered)) or str(delivered)
        return (f"{short_quality(opts.quality)} isn't available for this video, so the highest "
                f"available quality ({got}) was saved.")

    def finish(self, ok: bool, message: str) -> None:
        """Apply the job's on_done result. Idempotent per job: a repeat call is ignored."""
        if self._finished_job == self.job.id and self.state in FINISHED:
            log.warning("ignoring repeated on_done for job %s (ok=%s)", self.job.id, ok)
            return
        self._finished_job = self.job.id
        message = (message or "").strip()
        job_state = getattr(self.job, "state", "")
        if ok:
            self._finish_ok(message)
        elif self.cancel_requested or job_state == "cancelled":
            self.set_state("cancelled", "Cancelled")
            self._hide_message()
        else:
            self.set_state("error", "")
            self._show_message(message or "The download failed.", T.DANGER,
                               try_quality=self.suggested_quality(),
                               settings=self.needs_login())

    def _finish_ok(self, message: str) -> None:
        out = self.job.output_path or ""
        name = os.path.basename(out.rstrip("/\\")) if out else ""
        self.set_state("done", "" if name else "Saved to your download folder")

        delivered = getattr(self.job, "delivered_quality", None)
        if delivered:
            text = delivered_quality_text(str(delivered))
            self.saved_chip.set(f"Saved · {text}" if text else "Saved", "success")
            self.saved_chip.grid(row=0, column=1, sticky="w", padx=(6, 0))
        else:
            self.saved_chip.grid_forget()
        if name:
            self._detail_file = name
            self._fit_detail()  # refined by the debounced fit once the chips have sizes

        # On success the engine's message is normally the output path. Anything else is a
        # note (e.g. "Finished with errors: 2 of 12 item(s) failed") - never an error.
        notes: list[str] = []
        warn = False
        engine_notes: list[str] = []
        if message and message != out and not _looks_like_path(message):
            engine_notes.append(message.split(". Saved to")[0].strip())
        warning = str(getattr(self.job, "warning", None) or "").strip()
        if warning and warning not in engine_notes:
            engine_notes.append(warning)
            warn = True
        for text in filter(None, engine_notes):
            notes.append(text if text.endswith((".", "!", "?")) else text + ".")
            low = text.lower()
            warn = warn or any(w in low for w in ("error", "fail", "warning", "skipped",
                                                  "lower", "instead", "available"))
        shortfall = self.delivered_note()
        if shortfall:
            warn = True
            # Prefer the engine's own explanation when it already mentions the quality.
            got = re.match(r"\d+", str(delivered or ""))
            explained = any((got and got.group(0) in n) or "quality" in n.lower() for n in notes)
            if not explained:
                notes.append(shortfall)
        if getattr(self.job, "already_downloaded", False):
            notes.append("This file was already in your download folder, so it wasn't "
                         "downloaded again.")
        if notes:
            self._show_message("  ".join(notes), T.WARNING if warn else T.TEXT_2)
        else:
            self._hide_message()

    # ------------------------------------------------------------------ actions
    def cancel(self) -> None:
        self.page.cancel(self)

    def retry(self) -> None:
        self.page.retry(self)

    def remove(self) -> None:
        self.page.remove(self)

    def _show_file(self) -> None:
        path = self.job.output_path  # the exact file of *this* job (e.g. "... - 720p.mp4")
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
    def find_duplicate(self, url: str, options: JobOptions,
                       exclude: JobRow | None = None) -> JobRow | None:
        """A waiting/running row downloading the same media in the same format and quality."""
        sig = job_signature(url, options)
        for row in self.rows:
            if row is exclude or not (row.state == "queued" or row.state in ACTIVE):
                continue
            if job_signature(row.job.url, row.job.options) == sig:
                return row
        return None

    def add(self, url: str, title: str, options: JobOptions, has_video: bool = True) -> JobRow:
        """Queue a download. Raises :class:`DuplicateJobError` for an identical active job."""
        dup = self.find_duplicate(url, options)
        if dup is not None:
            log.info("duplicate ignored: %s %s (already job %s)", url,
                     describe_options(options), dup.job.id)
            raise DuplicateJobError(dup)
        job = DownloadJob(url, title, options, self.app.deps)
        log.info("queued job %s: url=%s kind=%s quality=%s mp3_bitrate=%s playlist=%s "
                 "out_dir=%s cookies=%s", job.id, url, options.kind, options.quality,
                 options.mp3_bitrate, options.playlist, options.out_dir,
                 options.cookies_browser)
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
        repeat = row._finished_job == job_id and row.state in FINISHED
        row.finish(ok, message)
        if repeat:
            return
        log.info("job %s finished: ok=%s state=%s output=%s delivered=%s message=%s", job_id, ok,
                 row.state, row.job.output_path, getattr(row.job, "delivered_quality", None),
                 truncate(message or "", 200))
        if row.state == "done" and self.app.current_page != "queue":
            note = " (lower quality - see queue)" if row.delivered_note() else ""
            self.app.toast(f"Downloaded: {truncate(row.job.title, 60)}{note}", "success",
                           "Show file", row._show_file)
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

    def retry(self, row: JobRow, quality: str | None = None) -> None:
        """Run a finished row again with the same options (optionally a different quality)."""
        old = row.job
        if row.state not in FINISHED:
            return
        # A fresh copy of the *same* options: the requested quality is kept exactly, and
        # nothing the previous run mutated (e.g. out_dir) leaks into the new job.
        options = copy.copy(old.options)
        if quality:
            if options.kind == "mp4":
                options.quality = quality
            elif options.kind == "mp3":
                options.mp3_bitrate = quality
        if self.find_duplicate(old.url, options, exclude=row) is not None:
            self.app.toast("Already in the queue", "info", "View queue",
                           lambda: self.app.show_page("queue"))
            return
        row.job = DownloadJob(old.url, old.title, options, self.app.deps)
        row.opts_chip.set(describe_options(options))
        log.info("retry job %s -> %s: url=%s kind=%s quality=%s mp3_bitrate=%s playlist=%s",
                 old.id, row.job.id, old.url, options.kind, options.quality,
                 options.mp3_bitrate, options.playlist)
        row.cancel_requested = False
        row._last_fraction = 0.0
        row.bar.set(0)
        row._hide_message()
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
