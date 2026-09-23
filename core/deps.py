"""Download, verify, version and update the external tools the app relies on.

Tools (all stored in ``TOOLS_DIR``):

* ``yt-dlp``  -> ``yt-dlp.exe`` (standalone build, can self-update)
* ``ffmpeg``  -> ``ffmpeg.exe`` + ``ffprobe.exe`` from yt-dlp/FFmpeg-Builds
* ``deno``    -> ``deno.exe`` JavaScript runtime (yt-dlp needs it for YouTube)

Only the standard library is used so that the frozen executable stays small.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.paths import TOOLS_DIR

TOOLS = ("yt-dlp", "ffmpeg", "deno")

ProgressCallback = Callable[[str, str, "float | None"], None]

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
USER_AGENT = "H190K-Downloader (+https://h190k.com)"
HTTP_TIMEOUT = 30
CHUNK = 256 * 1024
# The FFmpeg auto-build is republished daily and is ~190 MB; only offer an update when the
# installed build is at least this many days older than the latest one.
FFMPEG_UPDATE_MIN_AGE_DAYS = 30

_REPOS: dict[str, str] = {
    "yt-dlp": "yt-dlp/yt-dlp",
    "ffmpeg": "yt-dlp/FFmpeg-Builds",
    "deno": "denoland/deno",
}
_ASSETS: dict[str, str] = {
    "yt-dlp": "yt-dlp.exe",
    "ffmpeg": "ffmpeg-master-latest-win64-gpl.zip",
    "deno": "deno-x86_64-pc-windows-msvc.zip",
}
_CHECKSUM_ASSETS: dict[str, str] = {
    "yt-dlp": "SHA2-256SUMS",
    "ffmpeg": "checksums.sha256",
    "deno": "deno-x86_64-pc-windows-msvc.zip.sha256sum",
}


# Work dirs of installs running in this process; cleanup_temp() must not touch them.
_ACTIVE_WORK: set[str] = set()


class DependencyError(Exception):
    """Raised when a tool cannot be installed or updated. The message is user-presentable."""


def _run(args: list[str], timeout: float = 20) -> str:
    """Run a command hidden and return stdout (empty string on any failure)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _request(url: str, method: str = "GET", accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, method=method, headers=headers)


def _retrying(func: Callable[[], Any], attempts: int = 3) -> Any:
    """Call ``func`` retrying transient network errors (not HTTP 4xx responses)."""
    for attempt in range(attempts):
        try:
            return func()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
        time.sleep(1.0 * (attempt + 1))
    raise AssertionError("unreachable")


def _http_text(url: str, accept: str | None = None) -> str:
    def once() -> str:
        with urllib.request.urlopen(_request(url, accept=accept), timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")

    return _retrying(once)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _redirect_target(url: str) -> str:
    """Return the Location a URL redirects to (without following it)."""
    opener = urllib.request.build_opener(_NoRedirect)

    def once() -> str:
        try:
            with opener.open(_request(url, method="HEAD"), timeout=HTTP_TIMEOUT) as resp:
                return resp.geturl()
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400 and exc.headers.get("Location"):
                return str(exc.headers["Location"])
            raise

    return _retrying(once)


def _friendly_net_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return f"GitHub rate limit or access denied (HTTP {exc.code}); try again later"
        return f"HTTP error {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error ({exc.reason}); check your internet connection"
    if isinstance(exc, TimeoutError):
        return "connection timed out; check your internet connection"
    return str(exc) or exc.__class__.__name__


def _norm_version(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    return v[1:] if v[:1] in ("v", "V") and v[1:2].isdigit() else v


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


class DependencyManager:
    """Manage the external tools in ``tools_dir``."""

    def __init__(self, tools_dir: Path = TOOLS_DIR) -> None:
        self.tools_dir = Path(tools_dir)
        self.ytdlp_path: Path = self.tools_dir / "yt-dlp.exe"
        self.ffmpeg_dir: Path = self.tools_dir
        self.ffmpeg_path: Path = self.tools_dir / "ffmpeg.exe"
        self.ffprobe_path: Path = self.tools_dir / "ffprobe.exe"
        self.deno_path: Path = self.tools_dir / "deno.exe"
        self.versions_file: Path = self.tools_dir / "versions.json"
        self.cleanup_temp()

    def cleanup_temp(self) -> None:
        """Remove leftovers of interrupted installs (``.<tool>-*`` work dirs, ``*.part``, ``*.tmp``)."""
        if not self.tools_dir.is_dir():
            return
        for pattern in [f".{t}-*" for t in TOOLS] + ["*.part", "*.tmp"]:
            for p in self.tools_dir.glob(pattern):
                if str(p) in _ACTIVE_WORK:
                    continue
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                except OSError:
                    pass  # possibly in use by a concurrent install; ignore

    # ------------------------------------------------------------------ versions file
    def _read_versions(self) -> dict[str, Any]:
        try:
            with open(self.versions_file, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_version(self, tool: str, version: str | None) -> None:
        data = self._read_versions()
        data[tool] = version
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.versions_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.versions_file)

    # ------------------------------------------------------------------ status
    def _is_installed(self, tool: str) -> bool:
        if tool == "yt-dlp":
            return self.ytdlp_path.is_file()
        if tool == "ffmpeg":
            return self.ffmpeg_path.is_file() and self.ffprobe_path.is_file()
        if tool == "deno":
            return self.deno_path.is_file()
        raise KeyError(tool)

    def _tool_path(self, tool: str) -> Path:
        return {"yt-dlp": self.ytdlp_path, "ffmpeg": self.ffmpeg_path, "deno": self.deno_path}[tool]

    def installed_version(self, tool: str) -> str | None:
        """Return the installed version of ``tool`` or None if missing/unknown."""
        if not self._is_installed(tool):
            return None
        if tool == "yt-dlp":
            out = _run([str(self.ytdlp_path), "--version"]).strip()
            return out.splitlines()[0].strip() if out else self._read_versions().get(tool)
        if tool == "deno":
            m = re.search(r"deno\s+(\d+\.\d+\.\d+)", _run([str(self.deno_path), "--version"]))
            return m.group(1) if m else _norm_version(self._read_versions().get(tool))
        # ffmpeg: builds are identified by date
        stored = self._read_versions().get(tool)
        if stored:
            return str(stored)
        m = re.search(r"-(\d{4})(\d{2})(\d{2})\b", _run([str(self.ffmpeg_path), "-version"]))
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

    def status(self) -> dict[str, dict[str, Any]]:
        """Return ``{tool: {"installed", "version", "path"}}`` for every tool."""
        return {
            tool: {
                "installed": self._is_installed(tool),
                "version": self.installed_version(tool),
                "path": str(self._tool_path(tool)),
            }
            for tool in TOOLS
        }

    def missing(self) -> list[str]:
        return [t for t in TOOLS if not self._is_installed(t)]

    def is_ready(self) -> bool:
        return not self.missing()

    # ------------------------------------------------------------------ remote versions
    def _latest_release(self, tool: str) -> tuple[str | None, str | None]:
        """Return ``(tag, version)`` of the latest release. Either may be None if unknown.

        Uses the GitHub API first; when rate-limited/unavailable, falls back to following the
        ``/releases/latest`` redirect (which yields the tag but for ffmpeg not a usable version).
        """
        repo = _REPOS[tool]
        try:
            data = json.loads(
                _http_text(f"https://api.github.com/repos/{repo}/releases/latest",
                           accept="application/vnd.github+json")
            )
            tag = str(data.get("tag_name") or "") or None
            if tool == "ffmpeg":
                src = f"{data.get('name') or ''} {data.get('published_at') or ''}"
                m = re.search(r"(\d{4}-\d{2}-\d{2})", src)
                return tag, (m.group(1) if m else None)
            return tag, _norm_version(tag)
        except Exception:  # noqa: BLE001 - any API failure -> fallback
            pass
        try:
            final = _redirect_target(f"https://github.com/{repo}/releases/latest")
            m = re.search(r"/releases/tag/([^/?#]+)", final)
            if m:
                tag = urllib.parse.unquote(m.group(1))
                return tag, (None if tool == "ffmpeg" else _norm_version(tag))
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def check_updates(self) -> dict[str, dict[str, Any]]:
        """Compare installed and latest versions. Never raises; unknown values are None."""
        result: dict[str, dict[str, Any]] = {}
        for tool in TOOLS:
            current = self.installed_version(tool)
            try:
                _tag, latest = self._latest_release(tool)
            except Exception:  # noqa: BLE001
                latest = None
            result[tool] = {
                "current": current,
                "latest": latest,
                "update_available": self._needs_update(tool, current, latest),
            }
        return result

    @staticmethod
    def _needs_update(tool: str, current: str | None, latest: str | None) -> bool:
        if current is None:
            return True
        if latest is None:
            return False
        if tool == "ffmpeg":
            try:
                cur = time.mktime(time.strptime(current, "%Y-%m-%d"))
                new = time.mktime(time.strptime(latest, "%Y-%m-%d"))
            except ValueError:
                return current != latest
            return (new - cur) >= FFMPEG_UPDATE_MIN_AGE_DAYS * 86400
        try:
            return _version_tuple(latest) > _version_tuple(current)
        except ValueError:
            return latest != current

    # ------------------------------------------------------------------ download helpers
    def _download(self, url: str, dest: Path, tool: str, label: str,
                  progress: ProgressCallback | None) -> None:
        """Stream ``url`` into ``dest`` with progress; retries once on transient errors."""
        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(_request(url), timeout=HTTP_TIMEOUT) as resp, \
                        open(dest, "wb") as fh:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    last_emit = 0.0
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if progress and (now - last_emit > 0.15):
                            last_emit = now
                            mb = done / 1048576
                            if total:
                                progress(tool, f"Downloading {label}... {mb:.1f} / {total / 1048576:.1f} MB",
                                         min(done / total, 1.0))
                            else:
                                progress(tool, f"Downloading {label}... {mb:.1f} MB", None)
                    if total and done < total:
                        raise DependencyError(f"download of {label} was incomplete")
                if progress:
                    progress(tool, f"Downloaded {label}", 1.0)
                return
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in (403, 404, 429):
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError, DependencyError) as exc:
                last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        raise DependencyError(f"Could not download {label}: {_friendly_net_error(last_exc)}"
                              if last_exc else f"Could not download {label}")

    def _expected_sha256(self, tool: str, base: str) -> str | None:
        """Fetch the published SHA-256 for the asset; None when unavailable."""
        asset = _ASSETS[tool]
        try:
            text = _http_text(f"{base}/{_CHECKSUM_ASSETS[tool]}")
        except Exception:  # noqa: BLE001 - verification is best-effort
            return None
        for line in text.splitlines():
            if asset in line:
                m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
                if m:
                    return m.group(1).lower()
        hashes = re.findall(r"\b([0-9a-fA-F]{64})\b", text)
        return hashes[0].lower() if len(hashes) == 1 else None

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def _replace(src: Path, dst: Path) -> None:
        try:
            os.replace(src, dst)
        except PermissionError as exc:
            raise DependencyError(
                f"Cannot replace {dst.name}: it is in use. Close running downloads and try again."
            ) from exc

    def _install(self, tool: str, progress: ProgressCallback | None,
                 release: tuple[str | None, str | None] | None = None) -> str | None:
        """Download and install the latest ``tool``. Returns the installed version."""
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        tag, latest = release if release is not None else self._latest_release(tool)
        repo = _REPOS[tool]
        base = (f"https://github.com/{repo}/releases/download/{urllib.parse.quote(tag)}"
                if tag else f"https://github.com/{repo}/releases/latest/download")
        asset = _ASSETS[tool]
        if progress:
            progress(tool, f"Preparing {tool}...", None)
        work = Path(tempfile.mkdtemp(prefix=f".{tool}-", dir=self.tools_dir))
        _ACTIVE_WORK.add(str(work))
        try:
            archive = work / asset
            self._download(f"{base}/{asset}", archive, tool, tool, progress)
            expected = self._expected_sha256(tool, base)
            if expected:
                if progress:
                    progress(tool, f"Verifying {tool}...", None)
                if self._sha256(archive) != expected:
                    raise DependencyError(f"Downloaded {tool} failed checksum verification; please retry.")
            if tool == "yt-dlp":
                self._replace(archive, self.ytdlp_path)
            else:
                wanted = ("ffmpeg.exe", "ffprobe.exe") if tool == "ffmpeg" else ("deno.exe",)
                if progress:
                    progress(tool, f"Extracting {tool}...", None)
                extracted: dict[str, Path] = {}
                try:
                    with zipfile.ZipFile(archive) as zf:
                        for info in zf.infolist():
                            name = info.filename.rsplit("/", 1)[-1].lower()
                            if name in wanted and name not in extracted and not info.is_dir():
                                target = work / name
                                with zf.open(info) as src, open(target, "wb") as dst:
                                    shutil.copyfileobj(src, dst, CHUNK)
                                extracted[name] = target
                except zipfile.BadZipFile as exc:
                    raise DependencyError(f"Downloaded {tool} archive is corrupt; please retry.") from exc
                missing = [w for w in wanted if w not in extracted]
                if missing:
                    raise DependencyError(f"{', '.join(missing)} not found in the {tool} archive.")
                for name, path in extracted.items():
                    self._replace(path, self.tools_dir / name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            _ACTIVE_WORK.discard(str(work))

        version = latest
        if tool != "ffmpeg":
            version = self.installed_version(tool) or latest
        elif version is None:
            version = time.strftime("%Y-%m-%d")  # fallback: today's auto-build
        self._write_version(tool, version)
        if progress:
            progress(tool, f"{tool} {version or ''} installed".replace("  ", " "), 1.0)
        return version

    # ------------------------------------------------------------------ public actions
    def install_missing(self, progress: ProgressCallback | None = None) -> None:
        """Install every missing tool. Raises DependencyError listing all failures."""
        self.cleanup_temp()
        errors: list[str] = []
        for tool in self.missing():
            try:
                self._install(tool, progress)
            except DependencyError as exc:
                errors.append(f"{tool}: {exc}")
                if progress:
                    progress(tool, f"Failed: {exc}", None)
            except Exception as exc:  # noqa: BLE001 - convert to DependencyError
                msg = _friendly_net_error(exc)
                errors.append(f"{tool}: {msg}")
                if progress:
                    progress(tool, f"Failed: {msg}", None)
        if errors:
            raise DependencyError("Some tools could not be installed:\n" + "\n".join(errors))

    def update_all(self, progress: ProgressCallback | None = None) -> dict[str, str]:
        """Install missing tools and update outdated ones. Never raises; returns per-tool result."""
        results: dict[str, str] = {}
        for tool in TOOLS:
            try:
                current = self.installed_version(tool)
                if progress:
                    progress(tool, f"Checking {tool}...", None)
                tag, latest = self._latest_release(tool)
                if current is not None and latest is None:
                    results[tool] = "failed: could not determine the latest version (network or GitHub rate limit)"
                    continue
                if not self._needs_update(tool, current, latest):
                    results[tool] = "up to date"
                    if progress:
                        progress(tool, f"{tool} {current} is up to date", 1.0)
                    continue
                version = self._install(tool, progress, (tag, latest))
                results[tool] = f"updated to {version}"
            except DependencyError as exc:
                results[tool] = f"failed: {exc}"
            except Exception as exc:  # noqa: BLE001
                results[tool] = f"failed: {_friendly_net_error(exc)}"
            if progress and results[tool].startswith("failed"):
                progress(tool, results[tool], None)
        return results
