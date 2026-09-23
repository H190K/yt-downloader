"""Flicker-free theme switching: snapshot cross-fade + native title-bar color.

customtkinter re-colors a window by redrawing every widget one after another, so a plain
``set_appearance_mode()`` shows a half-light/half-dark window for a few hundred milliseconds.
:class:`ThemeTransition` hides that:

1. ``PrintWindow(PW_RENDERFULLCONTENT)`` copies the window's client area (only our window, never
   whatever is on top of it) into a bitmap.
2. A borderless, click-through, non-activating overlay showing that bitmap is placed exactly over
   the client area, directly above the main window in the z-order.
3. The theme is switched underneath and fully redrawn while the overlay hides it.
4. The overlay fades out (``-alpha`` 1 -> 0, cubic ease-out, ~220 ms) and is destroyed.

If anything is unavailable (not Windows, window minimized/hidden, capture failed) the theme is
simply switched instantly. Requests arriving during a transition are coalesced: only the latest
one is applied once the running fade has finished. A watchdog guarantees the overlay can never
get stuck on screen.

All Win32 calls use explicit ``argtypes``/``restype`` so handles are not truncated on 64-bit.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
import tkinter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from PIL import Image, ImageTk

log = logging.getLogger(__name__)

DURATION_MS = 220   # fade length
FRAME_MS = 16       # fade tick (~60 fps)
WATCHDOG_MS = 2500  # the overlay is force-removed after this, whatever happens
SYSTEM_POLL_MS = 1000

IS_WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------- Win32 bindings
class _Win32:
    """Lazily-initialized, fully typed Win32 entry points (``None`` when unavailable)."""

    GA_ROOT = 2
    GW_HWNDPREV = 3
    GWL_EXSTYLE = -20
    GWLP_HWNDPARENT = -8
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    SWP_NOOWNERZORDER = 0x0200
    HWND_TOP = 0
    HWND_TOPMOST = -1
    PW_CLIENTONLY = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002
    DWMWA_CLOAK = 13
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 before 20H1
    DIB_RGB_COLORS = 0

    def __init__(self) -> None:
        from ctypes import wintypes as w

        self.w = w
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        try:
            self.winmm: Any = ctypes.WinDLL("winmm")
            self.winmm.timeBeginPeriod.argtypes = [w.UINT]
            self.winmm.timeEndPeriod.argtypes = [w.UINT]
        except OSError:
            self.winmm = None

        def fn(lib: Any, name: str, res: Any, *args: Any) -> Any:
            f = getattr(lib, name)
            f.restype, f.argtypes = res, list(args)
            return f

        self.GetAncestor = fn(u, "GetAncestor", w.HWND, w.HWND, w.UINT)
        self.GetWindow = fn(u, "GetWindow", w.HWND, w.HWND, w.UINT)
        self.IsWindow = fn(u, "IsWindow", w.BOOL, w.HWND)
        self.IsWindowVisible = fn(u, "IsWindowVisible", w.BOOL, w.HWND)
        self.IsIconic = fn(u, "IsIconic", w.BOOL, w.HWND)
        self.GetWindowRect = fn(u, "GetWindowRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
        self.GetClientRect = fn(u, "GetClientRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
        self.ClientToScreen = fn(u, "ClientToScreen", w.BOOL, w.HWND, ctypes.POINTER(w.POINT))
        self.GetDC = fn(u, "GetDC", w.HDC, w.HWND)
        self.ReleaseDC = fn(u, "ReleaseDC", ctypes.c_int, w.HWND, w.HDC)
        self.PrintWindow = fn(u, "PrintWindow", w.BOOL, w.HWND, w.HDC, w.UINT)
        self.GetWindowLongPtrW = fn(u, "GetWindowLongPtrW", ctypes.c_ssize_t, w.HWND, ctypes.c_int)
        self.SetWindowLongPtrW = fn(u, "SetWindowLongPtrW", ctypes.c_ssize_t, w.HWND, ctypes.c_int,
                                    ctypes.c_ssize_t)
        self.SetWindowPos = fn(u, "SetWindowPos", w.BOOL, w.HWND, w.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT)
        self.CreateCompatibleDC = fn(g, "CreateCompatibleDC", w.HDC, w.HDC)
        self.CreateCompatibleBitmap = fn(g, "CreateCompatibleBitmap", w.HBITMAP, w.HDC,
                                         ctypes.c_int, ctypes.c_int)
        self.SelectObject = fn(g, "SelectObject", w.HGDIOBJ, w.HDC, w.HGDIOBJ)
        self.DeleteObject = fn(g, "DeleteObject", w.BOOL, w.HGDIOBJ)
        self.DeleteDC = fn(g, "DeleteDC", w.BOOL, w.HDC)
        self.GetDIBits = fn(g, "GetDIBits", ctypes.c_int, w.HDC, w.HBITMAP, w.UINT, w.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, w.UINT)
        self.DwmSetWindowAttribute = fn(self.dwm, "DwmSetWindowAttribute", ctypes.c_long, w.HWND,
                                        w.DWORD, ctypes.c_void_p, w.DWORD)
        self.DwmFlush = fn(self.dwm, "DwmFlush", ctypes.c_long)


_api: _Win32 | None = None
_api_failed = False


def _win32() -> _Win32 | None:
    global _api, _api_failed
    if _api is None and not _api_failed and IS_WINDOWS:
        try:
            _api = _Win32()
        except Exception:  # noqa: BLE001 - any failure just disables the native extras
            log.info("Win32 theme helpers unavailable", exc_info=True)
            _api_failed = True
    return _api


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


# --------------------------------------------------------------------------- helpers
def toplevel_hwnd(widget: tkinter.Misc) -> int | None:
    """Real top-level (wrapper) HWND of ``widget``'s window, or None."""
    api = _win32()
    if api is None:
        return None
    try:
        hwnd = api.GetAncestor(widget.winfo_id(), api.GA_ROOT)
    except (tkinter.TclError, OSError):
        return None
    return int(hwnd) if hwnd else None


def set_title_bar_dark(widget: tkinter.Misc, dark: bool) -> bool:
    """Make the native Windows title bar dark or light. Works on hidden windows too."""
    api = _win32()
    hwnd = toplevel_hwnd(widget) if api else None
    if api is None or hwnd is None:
        return False
    value = ctypes.c_int(1 if dark else 0)
    for attr in (api.DWMWA_USE_IMMERSIVE_DARK_MODE, api.DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            if api.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0:
                return True
        except OSError:
            break
    return False


def set_cloaked(widget: tkinter.Misc, cloaked: bool) -> bool:
    """Hide/show ``widget``'s top-level window at the compositor level (``DWMWA_CLOAK``).

    A cloaked window is mapped and paints normally but is not drawn on screen, so it can be
    shown and fully painted before the user sees its first frame (no white flash).
    """
    api = _win32()
    hwnd = toplevel_hwnd(widget) if api else None
    if api is None or hwnd is None:
        return False
    value = ctypes.c_int(1 if cloaked else 0)
    try:
        return api.DwmSetWindowAttribute(hwnd, api.DWMWA_CLOAK, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0
    except OSError:
        return False


def system_prefers_dark() -> bool:
    """True when the OS app theme is dark (False if unknown)."""
    try:
        import darkdetect  # customtkinter dependency

        return darkdetect.theme() == "Dark"
    except Exception:  # noqa: BLE001
        return False


def resolve_mode(setting: str | None) -> str:
    """Map the ``theme`` setting ("dark" / "light" / "system") to "dark" or "light"."""
    setting = (setting or "dark").lower()
    if setting == "system":
        return "dark" if system_prefers_dark() else "light"
    return "light" if setting == "light" else "dark"


@contextmanager
def batched_redraws() -> Iterator[None]:
    """Collapse the per-widget ``update_idletasks()`` flushes into a single one.

    customtkinter's ``CTkBaseClass._set_appearance_mode`` calls ``update_idletasks()`` after
    redrawing *each* widget. That is what paints the window piece by piece, and it roughly doubles
    the cost of a theme switch. While this context is active those calls are no-ops; one real
    flush runs at the end. Only for synchronous use on the Tk thread (worker threads never touch
    Tk in this app).
    """
    original = tkinter.Misc.update_idletasks
    tkinter.Misc.update_idletasks = _skip_idletasks  # type: ignore[method-assign,assignment]
    try:
        yield
    finally:
        tkinter.Misc.update_idletasks = original  # type: ignore[method-assign]


def _skip_idletasks(_self: tkinter.Misc) -> None:
    return None


def apply_appearance(root: tkinter.Misc, mode: str) -> None:
    """Switch customtkinter to ``mode`` in one batched redraw and match the Windows title bar."""
    import customtkinter as ctk

    with batched_redraws():
        ctk.set_appearance_mode(mode)
    set_title_bar_dark(root, mode == "dark")
    try:
        root.update_idletasks()
    except tkinter.TclError:
        pass


@dataclass(frozen=True)
class Snapshot:
    """Client-area pixels of a window and where that client area is on screen (physical px)."""

    image: Image.Image
    x: int
    y: int

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def capture_client(widget: tkinter.Misc) -> Snapshot | None:
    """Capture the client area of ``widget``'s top-level window with ``PrintWindow``.

    Renders only our window (not whatever overlaps it on screen). Returns None when the window is
    not visible, minimized or the capture fails. Coordinates are physical pixels (the process is
    per-monitor DPI aware, as is Tk's geometry), so the snapshot maps 1:1 onto the screen.
    """
    api = _win32()
    hwnd = toplevel_hwnd(widget) if api else None
    if api is None or hwnd is None:
        return None
    w = api.w
    if not api.IsWindowVisible(hwnd) or api.IsIconic(hwnd):
        return None
    wr, cr, origin = w.RECT(), w.RECT(), w.POINT(0, 0)
    if not (api.GetWindowRect(hwnd, ctypes.byref(wr)) and api.GetClientRect(hwnd, ctypes.byref(cr))
            and api.ClientToScreen(hwnd, ctypes.byref(origin))):
        return None
    win_w, win_h = wr.right - wr.left, wr.bottom - wr.top
    cw, ch = cr.right - cr.left, cr.bottom - cr.top
    if cw <= 0 or ch <= 0 or win_w <= 0 or win_h <= 0:
        return None

    # Client area directly (about twice as fast); whole window + crop as a fallback, since
    # PW_CLIENTONLY is not honored together with PW_RENDERFULLCONTENT on every build.
    image = _print_window(api, hwnd, cw, ch, api.PW_CLIENTONLY | api.PW_RENDERFULLCONTENT)
    if image is None or _is_black(image):
        full = _print_window(api, hwnd, win_w, win_h, api.PW_RENDERFULLCONTENT)
        if full is None:
            return None
        left, top = origin.x - wr.left, origin.y - wr.top
        image = full.crop((left, top, left + cw, top + ch))
    # A failed PrintWindow can "succeed" with an all-black bitmap; never show that.
    if _is_black(image):
        return None
    return Snapshot(image, origin.x, origin.y)


def _is_black(image: Image.Image) -> bool:
    extrema = cast("tuple[tuple[int, int], ...]", image.getextrema())  # RGB: one pair per band
    return max(hi for _lo, hi in extrema) == 0


def _print_window(api: _Win32, hwnd: int, width: int, height: int,
                  flags: int) -> Image.Image | None:
    """``PrintWindow`` into a ``width`` x ``height`` 24-bit RGB image (None on failure)."""
    screen_dc = api.GetDC(None)
    if not screen_dc:
        return None
    mem_dc = bmp = old = None
    try:
        mem_dc = api.CreateCompatibleDC(screen_dc)
        bmp = api.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bmp:
            return None
        old = api.SelectObject(mem_dc, bmp)
        if not api.PrintWindow(hwnd, mem_dc, flags):
            return None
        api.SelectObject(mem_dc, old)
        old = None
        # Top-down 32-bit DIB. BITMAPINFO = header + one RGBQUAD; reserve room for it.
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0,
                                   0, 0, 0, 0, 0)
        info = (ctypes.c_byte * (ctypes.sizeof(header) + 16))()
        ctypes.memmove(info, ctypes.byref(header), ctypes.sizeof(header))
        buf = ctypes.create_string_buffer(width * height * 4)
        if api.GetDIBits(mem_dc, bmp, 0, height, buf, info, api.DIB_RGB_COLORS) != height:
            return None
    finally:
        if old is not None and mem_dc:
            api.SelectObject(mem_dc, old)
        if bmp:
            api.DeleteObject(bmp)
        if mem_dc:
            api.DeleteDC(mem_dc)
        api.ReleaseDC(None, screen_dc)
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


# --------------------------------------------------------------------------- transition
class ThemeTransition:
    """Switch the appearance mode of ``root`` behind a fading snapshot of the old look.

    ``apply(mode)`` performs the real switch ("dark"/"light"); it is called exactly once per
    effective change, on the Tk thread.
    """

    def __init__(self, root: tkinter.Misc, apply: Callable[[str], None],
                 duration_ms: int = DURATION_MS, frame_ms: int = FRAME_MS) -> None:
        self.root = root
        self._apply = apply
        self.duration_ms = duration_ms
        self.frame_ms = frame_ms
        self.mode: str | None = None  # last applied mode
        self._overlay: tkinter.Toplevel | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._pending: str | None = None
        self._busy = False
        self._closed = False
        self._t0: float | None = None
        self._job: str | None = None
        self._watchdog: str | None = None
        self._timer_boost = False

    @property
    def busy(self) -> bool:
        return self._busy

    def switch(self, mode: str, animate: bool = True) -> None:
        """Switch to ``mode`` ("dark"/"light"), cross-fading when possible."""
        if self._closed:
            return
        if self._busy:
            self._pending = mode  # coalesce: only the latest request survives
            return
        if mode == self.mode:
            return
        self._busy = True
        try:
            snap = self._snapshot() if animate else None
            if snap is None or not self._show_overlay(snap):
                self._destroy_overlay()
                self._do_apply(mode)
                self._busy = False
                self._run_pending()
                return
            self._do_apply(mode)
            # Let every widget paint in the new colors while the overlay still hides them.
            self.root.update_idletasks()
            self._t0 = None
            self._watchdog = self.root.after(WATCHDOG_MS, self._finish)
            self._job = self.root.after(self.frame_ms, self._step)
        except tkinter.TclError:
            # The window is being destroyed mid-switch; just make sure nothing is left behind.
            self._finish(run_pending=False)
        except Exception:  # noqa: BLE001 - never let a cosmetic effect break theme switching
            log.exception("theme transition failed")
            self._finish(run_pending=False)
            if self.mode != mode:
                try:
                    self._do_apply(mode)
                except Exception:  # noqa: BLE001
                    log.exception("applying theme failed")

    def cancel(self) -> None:
        """Stop everything immediately (used when the app closes)."""
        self._closed = True
        self._pending = None
        self._finish(run_pending=False)

    # ------------------------------------------------------------------ internals
    def _do_apply(self, mode: str) -> None:
        self.mode = mode
        self._apply(mode)

    def _snapshot(self) -> Snapshot | None:
        try:
            # Flush pending redraws (e.g. the just-clicked selector) into the snapshot.
            self.root.update_idletasks()
            return capture_client(self.root)
        except Exception:  # noqa: BLE001
            log.info("theme snapshot failed", exc_info=True)
            return None

    def _show_overlay(self, snap: Snapshot) -> bool:
        api = _win32()
        if api is None:
            return False
        main_hwnd = toplevel_hwnd(self.root)
        if main_hwnd is None:
            return False
        # Remember which window is directly above ours *before* the overlay exists.
        above = api.GetWindow(main_hwnd, api.GW_HWNDPREV)

        top = tkinter.Toplevel(self.root)
        self._overlay = top
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=_avg_color(snap.image), bd=0, highlightthickness=0, takefocus=0)
        top.attributes("-alpha", 0.0)  # layered from the start; invisible until placed
        top.geometry(f"{snap.width}x{snap.height}+{snap.x}+{snap.y}")
        self._photo = ImageTk.PhotoImage(snap.image, master=top)
        canvas = tkinter.Canvas(top, width=snap.width, height=snap.height, bd=0,
                                highlightthickness=0, takefocus=0, bg=top.cget("bg"))
        canvas.create_image(0, 0, image=self._photo, anchor="nw")
        canvas.place(x=0, y=0, width=snap.width, height=snap.height)
        exposed: list[bool] = []
        # Bound on the toplevel: its tag is in every child's bindtags, so any expose counts.
        top.bind("<Expose>", lambda _e: exposed.append(True))
        top.update_idletasks()

        hwnd = toplevel_hwnd(top)
        if hwnd is None:
            return False
        # Owned by the main window (Tk ignores ``transient`` for override-redirect windows):
        # Windows then keeps it above its owner even if the main window is activated or raised
        # mid-fade, and hides it together with the owner.
        api.SetWindowLongPtrW(hwnd, api.GWLP_HWNDPARENT, main_hwnd)
        # Click-through, never activated, no taskbar button.
        ex = api.GetWindowLongPtrW(hwnd, api.GWL_EXSTYLE)
        api.SetWindowLongPtrW(hwnd, api.GWL_EXSTYLE,
                              ex | api.WS_EX_LAYERED | api.WS_EX_TRANSPARENT
                              | api.WS_EX_NOACTIVATE | api.WS_EX_TOOLWINDOW)
        top.deiconify()
        top.update_idletasks()  # deiconify of a never-mapped toplevel is performed at idle time
        # Sit directly above the main window: above it, but below anything covering it (the
        # switch may come from the OS while we are in the background). Not topmost on purpose.
        insert_after = api.HWND_TOP
        if api.GetWindowLongPtrW(main_hwnd, api.GWL_EXSTYLE) & api.WS_EX_TOPMOST:
            insert_after = api.HWND_TOPMOST  # main window is "always on top": stay above it
        elif above and api.IsWindow(above) and not (
                api.GetWindowLongPtrW(above, api.GWL_EXSTYLE) & api.WS_EX_TOPMOST):
            insert_after = above
        api.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                         api.SWP_NOMOVE | api.SWP_NOSIZE | api.SWP_NOACTIVATE)
        # The overlay only reaches the screen once Tk has handled its map/expose *window events*
        # (update_idletasks() alone is not enough, and the main loop won't run until the theme
        # switch is over). Process just those -- no timers, idle or file handlers, so nothing
        # else in the app runs re-entrantly -- until the canvas has been exposed.
        if not _pump_window_events(top, until=lambda: bool(exposed)):
            log.info("theme overlay was not exposed in time; switching without cross-fade")
            return False
        top.update_idletasks()  # paint the snapshot
        top.attributes("-alpha", 1.0 - 1 / 255)  # visually opaque, stays a layered window
        top.update_idletasks()
        _dwm_flush(api)  # overlay is composited before the first half-redrawn pixel can be
        self._boost_timer(True)
        return True

    def _step(self) -> None:
        self._job = None
        top = self._overlay
        if top is None:
            return
        now = time.perf_counter()
        if self._t0 is None:
            # First tick: the new theme has had one event-loop pass to paint; start fading.
            self._t0 = now
        t = min(1.0, (now - self._t0) * 1000.0 / self.duration_ms)
        if t >= 1.0:
            self._finish()
            return
        alpha = (1.0 - t) ** 3  # overlay opacity: cubic ease-out of the reveal
        try:
            top.attributes("-alpha", max(0.0, min(alpha, 1.0 - 1 / 255)))
            self._job = self.root.after(self.frame_ms, self._step)
        except tkinter.TclError:
            self._finish(run_pending=False)

    def _finish(self, run_pending: bool = True) -> None:
        for attr in ("_job", "_watchdog"):
            job = getattr(self, attr)
            setattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:  # noqa: BLE001 - root already gone
                    log.debug("after_cancel failed", exc_info=True)
        self._destroy_overlay()
        self._boost_timer(False)
        self._busy = False
        self._t0 = None
        if run_pending:
            self._run_pending()

    def _run_pending(self) -> None:
        mode, self._pending = self._pending, None
        if mode is None or mode == self.mode or self._closed:
            return
        try:
            self.root.after(1, lambda: self.switch(mode))
        except tkinter.TclError:
            pass

    def _destroy_overlay(self) -> None:
        top, self._overlay = self._overlay, None
        if top is not None:
            try:
                top.destroy()
            except Exception:  # noqa: BLE001 - interpreter may already be gone
                log.debug("overlay destroy failed", exc_info=True)
        self._photo = None

    def _boost_timer(self, on: bool) -> None:
        """1 ms timer resolution while fading (Tk's after() is ~15.6 ms-granular otherwise)."""
        api = _win32()
        if api is None or api.winmm is None or on == self._timer_boost:
            return
        try:
            (api.winmm.timeBeginPeriod if on else api.winmm.timeEndPeriod)(1)
            self._timer_boost = on
        except OSError:
            pass


def _avg_color(image: Image.Image) -> str:
    pixel = cast("tuple[int, int, int]", image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))
    r, g, b = pixel[:3]
    return f"#{r:02x}{g:02x}{b:02x}"


def _pump_window_events(widget: tkinter.Misc, until: Callable[[], bool],
                        max_ms: float = 150.0) -> bool:
    """Process pending Tk *window* events only, until ``until()`` is true or ``max_ms`` passed."""
    import _tkinter

    flags = _tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT
    deadline = time.perf_counter() + max_ms / 1000.0
    while not until():
        if time.perf_counter() > deadline:
            return False
        if not widget.tk.dooneevent(flags):
            time.sleep(0.001)  # the paint message has not been posted yet
    return True


def paint_now(widget: tkinter.Misc, max_ms: float = 300.0) -> bool:
    """Synchronously give a just-mapped toplevel its first complete paint.

    Processes only *window* events (map/expose) and idle redraws -- never timers or file
    handlers, unlike ``update()`` which can spin indefinitely while self-rescheduling ``after``
    loops keep becoming due. Returns False if the window was not exposed within ``max_ms``.
    """
    import _tkinter

    exposed: list[bool] = []
    tag = f"paint_now{id(widget)}"
    widget.bind_class(tag, "<Expose>", lambda _e: exposed.append(True))
    widget.bindtags((tag, *widget.bindtags()))
    try:
        widget.update_idletasks()  # performs a pending map
        ok = _pump_window_events(widget, until=lambda: bool(exposed), max_ms=max_ms)
        deadline = time.perf_counter() + max_ms / 1000.0
        flags = _tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT
        while widget.tk.dooneevent(flags) and time.perf_counter() < deadline:
            pass  # remaining expose/configure events that are already queued
        widget.update_idletasks()  # the actual (idle-time) redraws
        api = _win32()
        if api is not None:
            _dwm_flush(api)
        return ok
    finally:
        try:
            widget.bindtags(tuple(t for t in widget.bindtags() if t != tag))
            widget.unbind_class(tag, "<Expose>")
        except tkinter.TclError:
            pass


def _dwm_flush(api: _Win32) -> None:
    try:
        api.DwmFlush()
    except OSError:
        pass
