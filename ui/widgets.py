"""Reusable composite widgets built on customtkinter."""
from __future__ import annotations

from typing import Any, Callable, Sequence

import customtkinter as ctk

from ui import theme as T
from ui.util import open_url


def bind_tree(widget: Any, sequence: str, func: Callable[[Any], Any]) -> None:
    """Bind ``func`` to ``widget`` and all of its descendants."""
    widget.bind(sequence, func, add="+")
    for child in widget.winfo_children():
        bind_tree(child, sequence, func)


def set_cursor(widget: Any, cursor: str) -> None:
    try:
        widget.configure(cursor=cursor)
    except Exception:  # noqa: BLE001 - some CTk widgets reject cursor on configure
        pass
    for child in widget.winfo_children():
        set_cursor(child, cursor)


def pointer_inside(widget: Any) -> bool:
    try:
        x, y = widget.winfo_pointerxy()
        wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
        return wx <= x < wx + widget.winfo_width() and wy <= y < wy + widget.winfo_height()
    except Exception:  # noqa: BLE001 - widget destroyed
        return False


class Card(ctk.CTkFrame):
    """Rounded surface with a hairline border."""

    def __init__(self, master: Any, **kw: Any) -> None:
        kw.setdefault("fg_color", T.CARD)
        kw.setdefault("border_color", T.CARD_BORDER)
        kw.setdefault("border_width", 1)
        kw.setdefault("corner_radius", 12)
        super().__init__(master, **kw)


def fit_text(text: str, fnt: Any, max_px: int) -> str:
    """Shorten ``text`` with an ellipsis so it renders within ``max_px`` pixels in ``fnt``."""
    text = " ".join(text.split())
    if max_px <= 0 or fnt.measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.measure(text[:mid].rstrip() + "…") <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


class ScrollArea(ctk.CTkScrollableFrame):
    """Transparent scrollable frame whose scrollbar only shows when content overflows."""

    def __init__(self, master: Any, **kw: Any) -> None:
        kw.setdefault("fg_color", "transparent")
        kw.setdefault("scrollbar_button_color", T.SCROLL)
        kw.setdefault("scrollbar_button_hover_color", T.SCROLL_HOVER)
        super().__init__(master, **kw)
        self._bar_visible = True
        self._check_job: str | None = None
        self.bind("<Configure>", lambda _e: self._schedule_check(), add="+")
        self._parent_canvas.bind("<Configure>", lambda _e: self._schedule_check(), add="+")

    def _schedule_check(self) -> None:
        if self._check_job is None:
            self._check_job = self.after(30, self._check)

    def _check(self) -> None:
        self._check_job = None
        try:
            needed = self.winfo_reqheight() > self._parent_canvas.winfo_height() + 1
            if needed and not self._bar_visible:
                self._scrollbar.grid()
                self._bar_visible = True
            elif not needed and self._bar_visible:
                self._scrollbar.grid_remove()
                self._parent_canvas.yview_moveto(0)
                self._bar_visible = False
        except Exception:  # noqa: BLE001 - relies on CTk internals; cosmetic only
            pass

    def _mouse_wheel_all(self, event: Any) -> None:  # noqa: D401 - CTk override
        if self._bar_visible:
            super()._mouse_wheel_all(event)


class PageHeader(ctk.CTkFrame):
    """Page title + subtitle with an ``actions`` frame on the right."""

    def __init__(self, master: Any, title: str, subtitle: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        T.label(self, title, 24, "semibold").grid(row=0, column=0, sticky="w")
        self.subtitle = T.label(self, subtitle, 13, color=T.TEXT_2)
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        self.actions.grid(row=0, column=1, rowspan=2, sticky="e")


class Chip(ctk.CTkLabel):
    """Small rounded pill label."""

    STYLES: dict[str, tuple[T.Color, T.Color]] = {
        "neutral": (T.CONTROL, T.TEXT_2),
        "accent": (T.ACCENT_SOFT, T.ACCENT_TEXT),
        "success": (T.SUCCESS_SOFT, T.SUCCESS),
        "warning": (T.WARNING_SOFT, T.WARNING),
        "danger": (T.DANGER_SOFT, T.DANGER),
    }

    def __init__(self, master: Any, text: str = "", style: str = "neutral", **kw: Any) -> None:
        fg, color = self.STYLES[style]
        self._style = style
        super().__init__(master, text=text, fg_color=fg, text_color=color, corner_radius=6,
                         font=T.font(11, "semibold"), height=22, padx=8, **kw)

    def set(self, text: str, style: str | None = None) -> None:
        if style and style != self._style:
            self._style = style
            fg, color = self.STYLES[style]
            self.configure(fg_color=fg, text_color=color)
        if text != self.cget("text"):
            self.configure(text=text)


class Dot(ctk.CTkFrame):
    def __init__(self, master: Any, color: T.Color = T.SUCCESS, size: int = 8) -> None:
        super().__init__(master, width=size, height=size, corner_radius=size // 2, fg_color=color)

    def set_color(self, color: T.Color) -> None:
        self.configure(fg_color=color)


class LinkLabel(ctk.CTkLabel):
    """Accent-colored clickable text that opens a URL (underlined on hover)."""

    def __init__(self, master: Any, text: str, url: str | None = None, size: int = 13,
                 color: T.Color = T.ACCENT_TEXT, command: Callable[[], Any] | None = None,
                 weight: str = "normal", **kw: Any) -> None:
        self._font = T.font(size, weight)
        self._font_hover = ctk.CTkFont(family=self._font.cget("family"), size=size,
                                       weight=self._font.cget("weight"), underline=True)
        super().__init__(master, text=text, text_color=color, font=self._font, cursor="hand2",
                         **kw)
        self._action = command or (lambda: open_url(url) if url else None)
        self.bind("<Button-1>", lambda _e: self._action())
        self.bind("<Enter>", lambda _e: self.configure(font=self._font_hover))
        self.bind("<Leave>", lambda _e: self.configure(font=self._font))


class NavItem(ctk.CTkFrame):
    """Sidebar navigation entry: icon + label + optional count badge."""

    def __init__(self, master: Any, glyph: str, text: str, command: Callable[[], Any]) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=8, height=40)
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._selected = False
        self._styled = False
        self._command = command
        self.icon = T.glyph(self, glyph, 16, T.TEXT_2, width=24)
        self.icon.grid(row=0, column=0, padx=(14, 8))
        self.text = T.label(self, text, 13, "semibold", T.TEXT_2)
        self.text.grid(row=0, column=1, sticky="w")
        self.badge = ctk.CTkLabel(self, text="", fg_color=T.ACCENT, text_color=T.ON_ACCENT,
                                  corner_radius=9, height=18, width=22, font=T.font(10, "bold"))
        bind_tree(self, "<Button-1>", lambda _e: self._command())
        bind_tree(self, "<Enter>", self._on_enter)
        bind_tree(self, "<Leave>", self._on_leave)
        set_cursor(self, "hand2")

    def _on_enter(self, _e: Any = None) -> None:
        if not self._selected:
            self.configure(fg_color=T.HOVER)

    def _on_leave(self, _e: Any = None) -> None:
        self.after(15, self._check_leave)

    def _check_leave(self) -> None:
        if not self._selected and not pointer_inside(self):
            self.configure(fg_color="transparent")

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected and self._styled:
            return
        self._styled = True
        self._selected = selected
        self.configure(fg_color=T.ACCENT_SOFT if selected else "transparent")
        color = T.ACCENT_TEXT if selected else T.TEXT_2
        self.icon.configure(text_color=color)
        self.text.configure(text_color=T.TEXT if selected else T.TEXT_2)

    def set_badge(self, count: int) -> None:
        if count == getattr(self, "_badge_count", None):
            return
        self._badge_count = count
        if count > 0:
            self.badge.configure(text=str(count) if count < 100 else "99+")
            self.badge.grid(row=0, column=2, padx=(0, 12))
        else:
            self.badge.grid_forget()


class SlidingIndicator(ctk.CTkFrame):
    """Small accent bar that glides to the selected nav item (after()-driven, ease-out)."""

    DURATION_MS = 180
    FRAME_MS = 15
    HEIGHT = 18

    def __init__(self, master: Any) -> None:
        super().__init__(master, width=3, height=self.HEIGHT, corner_radius=2, fg_color=T.ACCENT)
        self._y: float | None = None
        self._job: str | None = None

    def move_to(self, target: Any, animate: bool = True) -> None:
        if not target.winfo_ismapped() or target.winfo_height() <= 1:
            self.after(30, lambda: self.move_to(target, animate=False))
            return
        scaling = self._get_widget_scaling() or 1.0
        goal = (target.winfo_y() + (target.winfo_height() - self.HEIGHT * scaling) / 2) / scaling
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if self._y is None or not animate:
            self._set(goal)
            return
        start, steps = self._y, max(1, self.DURATION_MS // self.FRAME_MS)

        def step(i: int = 1) -> None:
            t = i / steps
            eased = 1 - (1 - t) ** 3
            self._set(start + (goal - start) * eased)
            self._job = self.after(self.FRAME_MS, step, i + 1) if i < steps else None
        step()

    def _set(self, y: float) -> None:
        self._y = y
        self.place(x=2, y=round(y))
        self.lift()


class TileSelector(ctk.CTkFrame):
    """Row of large selectable tiles (icon + title + caption)."""

    def __init__(self, master: Any, options: Sequence[tuple[str, str, str, str]],
                 command: Callable[[str], Any] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self._command = command
        self._value: str | None = None
        self._tiles: dict[str, dict[str, Any]] = {}
        self._visible: list[str] = []
        for value, glyph, title, caption in options:
            tile = ctk.CTkFrame(self, fg_color=T.FIELD, border_color=T.FIELD_BORDER,
                                border_width=1, corner_radius=10, height=58, width=150)
            tile.grid_propagate(False)
            tile.grid_columnconfigure(1, weight=1)
            tile.grid_rowconfigure((0, 1), weight=1)
            icon = T.glyph(tile, glyph, 18, T.TEXT_2, width=26)
            icon.grid(row=0, column=0, rowspan=2, padx=(12, 8))
            t1 = T.label(tile, title, 13, "semibold", T.TEXT, height=18)
            t1.grid(row=0, column=1, sticky="sw", padx=(0, 10), pady=(9, 0))
            t2 = T.label(tile, caption, 11, color=T.TEXT_2, height=16)
            t2.grid(row=1, column=1, sticky="nw", padx=(0, 10), pady=(0, 9))
            bind_tree(tile, "<Button-1>", lambda _e, v=value: self._click(v))
            bind_tree(tile, "<Enter>", lambda _e, v=value: self._hover(v, True))
            bind_tree(tile, "<Leave>", lambda _e, v=value: self.after(15, self._hover_check, v))
            set_cursor(tile, "hand2")
            self._tiles[value] = {"frame": tile, "icon": icon, "title": t1}
        self.set_visible([o[0] for o in options])

    def set_visible(self, values: Sequence[str]) -> None:
        self._visible = list(values)
        for parts in self._tiles.values():
            parts["frame"].grid_forget()
        for i, value in enumerate(self._visible):
            self._tiles[value]["frame"].grid(row=0, column=i, padx=(0 if i == 0 else 10, 0),
                                             sticky="ew")
        if self._value not in self._visible and self._visible:
            self.set(self._visible[0])

    def _click(self, value: str) -> None:
        if value != self._value:
            self.set(value)
            if self._command:
                self._command(value)

    def _hover(self, value: str, inside: bool) -> None:
        if value == self._value:
            return
        self._tiles[value]["frame"].configure(border_color=T.ACCENT if inside else T.FIELD_BORDER)

    def _hover_check(self, value: str) -> None:
        if not pointer_inside(self._tiles[value]["frame"]):
            self._hover(value, False)

    def get(self) -> str | None:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
        for v, parts in self._tiles.items():
            sel = v == value
            parts["frame"].configure(fg_color=T.ACCENT_SOFT if sel else T.FIELD,
                                     border_color=T.ACCENT if sel else T.FIELD_BORDER,
                                     border_width=2 if sel else 1)
            parts["icon"].configure(text_color=T.ACCENT_TEXT if sel else T.TEXT_2)


class Toast(Card):
    """Transient notification placed at the bottom of the content area."""

    KINDS: dict[str, tuple[str, T.Color]] = {
        "info": (T.Icon.INFO, T.ACCENT_TEXT),
        "success": (T.Icon.CHECK, T.SUCCESS),
        "error": (T.Icon.ERROR, T.DANGER),
        "warning": (T.Icon.WARNING, T.WARNING),
    }

    def __init__(self, master: Any) -> None:
        super().__init__(master, corner_radius=10, border_color=T.FIELD_BORDER)
        self.grid_columnconfigure(1, weight=1)
        self.icon = T.glyph(self, T.Icon.INFO, 16, T.ACCENT_TEXT, width=22)
        self.icon.grid(row=0, column=0, padx=(14, 6), pady=10)
        self.msg = T.label(self, "", 13, wraplength=520)
        self.msg.grid(row=0, column=1, sticky="w", pady=10)
        self.action = ctk.CTkButton(self, text="", height=28, width=0, corner_radius=6,
                                    fg_color="transparent", hover_color=T.ACCENT_SOFT,
                                    text_color=T.ACCENT_TEXT, font=T.font(12, "semibold"))
        self.close = T.icon_button(self, T.Icon.CLOSE, self.hide, size=28)
        self.close.grid(row=0, column=3, padx=(4, 8))
        self._job: str | None = None

    def show(self, message: str, kind: str = "info", action_text: str | None = None,
             action: Callable[[], Any] | None = None, duration_ms: int | None = None) -> None:
        glyph, color = self.KINDS.get(kind, self.KINDS["info"])
        self.icon.configure(text=glyph, text_color=color)
        self.msg.configure(text=message)
        if action_text and action:
            def run() -> None:
                self.hide()
                action()
            self.action.configure(text=action_text, command=run)
            self.action.grid(row=0, column=2, padx=(10, 0))
        else:
            self.action.grid_forget()
        if self._job:
            self.after_cancel(self._job)
        self.place(relx=0.5, rely=0.0, y=14, anchor="n")
        self.lift()
        ms = duration_ms or (9000 if kind == "error" else 4500)
        self._job = self.after(ms, self.hide)

    def hide(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        self.place_forget()
