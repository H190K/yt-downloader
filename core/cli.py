"""Command-line mode: ``--setup``, ``--check``, ``--update``, ``--download URL``."""
from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Any, TextIO

from core import APP_NAME

_allocated_console = False


def _ensure_console() -> None:
    """Give a windowed (frozen, no-console) exe a console to print to.

    Tries to attach to the parent's console (e.g. the cmd/PowerShell the user typed the command
    in); if there is none, allocates a new console window.
    """
    global _allocated_console
    if sys.platform != "win32":
        return
    if sys.stdout is not None and sys.stderr is not None:
        try:
            sys.stdout.fileno()
            return
        except (OSError, ValueError, AttributeError):
            pass
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    ATTACH_PARENT_PROCESS = -1
    if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS) and kernel32.AllocConsole():
        _allocated_console = True
    try:
        out: TextIO = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115 - process-lifetime console
        sys.stdout = out
        sys.stderr = out
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")  # noqa: SIM115
        if not _allocated_console:
            out.write("\n")  # parent prompt was already printed; start on a fresh line
    except OSError:
        pass


def _setup_stdio() -> None:
    _ensure_console()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


class _ToolProgress:
    """Throttled console printer for DependencyManager progress callbacks."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._inline = False

    def __call__(self, tool: str, message: str, fraction: float | None) -> None:
        now = time.monotonic()
        is_step = fraction is not None and 0 < fraction < 1
        if is_step and now - self._last.get(tool, 0) < (0.2 if self._tty else 2.0):
            return
        self._last[tool] = now
        line = f"[{tool}] {message}"
        if fraction is not None:
            line += f" ({fraction * 100:.0f}%)"
        if self._tty and is_step:
            print("\r" + line.ljust(78)[:120], end="", flush=True)
            self._inline = True
        else:
            if self._inline:
                print()
                self._inline = False
            print(line, flush=True)


def _cmd_check() -> int:
    from core.deps import DependencyManager

    deps = DependencyManager()
    print(f"{APP_NAME} - tools in {deps.tools_dir}")
    status = deps.status()
    for tool, st in status.items():
        state = (st["version"] or "installed (version unknown)") if st["installed"] else "MISSING"
        print(f"  {tool:<7} {state}")
    print("Checking for updates...")
    updates = deps.check_updates()
    for tool, info in updates.items():
        latest = info["latest"] or "unknown (offline or rate-limited)"
        flag = "  <- update available" if info["update_available"] else ""
        print(f"  {tool:<7} current: {info['current'] or '-'}  latest: {latest}{flag}")
    return 0 if deps.is_ready() else 1


def _cmd_setup() -> int:
    from core.deps import DependencyError, DependencyManager

    deps = DependencyManager()
    missing = deps.missing()
    if not missing:
        print("All tools are already installed.")
        return 0
    print(f"Installing: {', '.join(missing)} -> {deps.tools_dir}")
    try:
        deps.install_missing(_ToolProgress())
    except DependencyError as exc:
        print(f"\nSetup failed:\n{exc}", file=sys.stderr)
        return 1
    print("\nSetup complete.")
    for tool, st in deps.status().items():
        print(f"  {tool:<7} {st['version']}")
    return 0


def _cmd_update() -> int:
    from core.deps import DependencyManager

    deps = DependencyManager()
    print(f"Updating tools in {deps.tools_dir} ...")
    results = deps.update_all(_ToolProgress())
    print()
    ok = True
    for tool, result in results.items():
        print(f"  {tool:<7} {result}")
        ok = ok and not result.startswith("failed")
    try:
        from core.config import load_config, save_config

        cfg = load_config()
        cfg["last_update_check"] = time.time()
        save_config(cfg)
    except OSError:
        pass
    return 0 if ok else 1


def _cmd_download(ns: argparse.Namespace) -> int:
    from core.config import load_config
    from core.deps import DependencyError, DependencyManager
    from core.engine import DownloadJob, EngineError, JobOptions, fetch_info

    cfg = load_config()
    deps = DependencyManager()
    if not deps.is_ready():
        print("Installing required tools first...")
        try:
            deps.install_missing(_ToolProgress())
        except DependencyError as exc:
            print(f"Setup failed:\n{exc}", file=sys.stderr)
            return 1
    kind = "mp3" if ns.mp3 else "m4a" if ns.m4a else "mp4"
    cookies = ns.cookies or cfg.get("cookies_browser")
    title = ns.url
    try:
        info = fetch_info(ns.url, deps, cookies)
        title = info.title
        extra = f", playlist of {info.entry_count}" if info.is_playlist else ""
        print(f"{info.title}  [{info.extractor}{extra}]")
    except EngineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    options = JobOptions(
        kind=kind,
        quality=str(ns.quality or "best"),
        mp3_bitrate=str(ns.bitrate or cfg.get("mp3_bitrate") or "320"),
        out_dir=ns.out or cfg["download_dir"],
        playlist=bool(ns.playlist),
        cookies_browser=cookies,
    )
    job = DownloadJob(ns.url, title, options, deps)
    done = threading.Event()
    result: dict[str, object] = {}
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    last: dict[str, Any] = {"t": 0.0, "msg": ""}

    def on_progress(_job: DownloadJob, p: dict) -> None:
        now = time.monotonic()
        item = f"[{p['item']}] " if p.get("item") else ""
        if p["status"] == "processing":
            line = f"{item}{p['message']}"
        else:
            line = f"{item}{p['message']} {p['percent']} {p['speed']} ETA {p['eta'] or '?'}"
        if tty:
            print("\r" + line.ljust(90)[:120], end="", flush=True)
        elif line != last["msg"] and (now - last["t"] > 2 or p["status"] == "processing"):
            print(line, flush=True)
            last["t"], last["msg"] = now, line

    def on_done(_job: DownloadJob, ok: bool, message: str) -> None:
        result["ok"], result["message"] = ok, message
        done.set()

    job.start(on_progress, on_done)
    try:
        while not done.wait(0.25):
            pass
    except KeyboardInterrupt:
        print("\nCancelling...")
        job.cancel()
        done.wait(30)
        return 130
    if tty:
        print()
    if result.get("ok"):
        # Files are named "<title> [<id>] - <quality>.<ext>" (e.g. "- 720p.mp4", "- 320kbps.mp3").
        quality = f" ({job.delivered_quality})" if job.delivered_quality else ""
        verb = "Already downloaded" if job.already_downloaded else "Saved"
        message = str(result.get("message") or "")
        print(f"{verb}{quality}: {job.output_path or message}")
        if message.lower().startswith("finished with errors"):
            print(message.split(". Saved to")[0] + ".")
        if job.warning:
            print(f"Warning: {job.warning}")
        return 0
    print(f"Error: {result.get('message')}", file=sys.stderr)
    return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="H190K Downloader", description=f"{APP_NAME} command-line mode")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true", help="install missing tools and update all tools")
    g.add_argument("--check", action="store_true", help="show installed tool versions and available updates")
    g.add_argument("--setup", action="store_true", help="install missing tools only")
    g.add_argument("--download", metavar="URL", dest="url", help="download a URL (default: MP4)")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--mp3", action="store_true", help="extract audio as MP3")
    fmt.add_argument("--m4a", action="store_true", help="extract audio as M4A")
    p.add_argument("--quality", choices=["best", "2160", "1440", "1080", "720", "480", "360"],
                   default="best", help="max video height for MP4 (default: best)")
    p.add_argument("--bitrate", choices=["320", "256", "192", "128"], help="MP3 bitrate in kbps")
    p.add_argument("--out", metavar="DIR", help="output folder (default: from settings)")
    p.add_argument("--playlist", action="store_true", help="download the whole playlist")
    p.add_argument("--cookies", choices=["chrome", "edge", "firefox", "brave", "opera"],
                   help="read login cookies from this browser")
    return p


def run_cli(argv: list[str]) -> int:
    """Entry point for command-line mode. Returns a process exit code."""
    _setup_stdio()
    try:
        try:
            ns = _parser().parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if ns.check:
            return _cmd_check()
        if ns.setup:
            return _cmd_setup()
        if ns.update:
            return _cmd_update()
        return _cmd_download(ns)
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            sys.stdout.flush()
        except (OSError, ValueError, AttributeError):
            pass
        if _allocated_console:
            try:
                input("\nPress Enter to close...")
            except (EOFError, OSError, RuntimeError):
                pass


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
