"""Install tools that were downloaded by someone else (the Inno Setup wizard) into ``TOOLS_DIR``.

Entry point: ``H190K Downloader.exe --import-tools <dir>`` (dispatched by ``main.py`` *before*
``core.cli``, so no console is ever attached or allocated). The installer downloads the release
assets into its ``{tmp}`` folder with its own progress page, because Inno Setup cannot unzip, and
then runs this hidden. Everything is logged to ``DATA_DIR/import-tools.log``.

Expected files in ``<dir>`` (any subset; missing ones are skipped):

* ``yt-dlp.exe``                           (+ optional ``SHA2-256SUMS``)
* ``ffmpeg-master-latest-win64-gpl.zip``   (+ optional ``checksums.sha256``)
* ``deno-x86_64-pc-windows-msvc.zip``      (+ optional ``deno-x86_64-pc-windows-msvc.zip.sha256sum``)

Each tool is verified (published SHA-256 from the local checksum file, else fetched from GitHub like
``core.deps`` does, and always a ``--version`` smoke test), then swapped in atomically with
``os.replace`` and recorded in ``versions.json`` exactly as :class:`core.deps.DependencyManager`
records it, so ``status()`` / ``is_ready()`` / ``check_updates()`` treat it as installed.

Exit codes: 0 = every provided tool imported, 1 = at least one tool failed,
2 = bad arguments / nothing to import.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from core.deps import (
    _ACTIVE_WORK,
    _ASSETS,
    _CHECKSUM_ASSETS,
    _REPOS,
    CHUNK,
    TOOLS,
    DependencyError,
    DependencyManager,
    _run,
)
from core.paths import DATA_DIR, TOOLS_DIR

LOG_FILE = DATA_DIR / "import-tools.log"
_LOG_MAX_BYTES = 256 * 1024
_RUN_TIMEOUT = 90  # first start of yt-dlp.exe unpacks itself; antivirus scans can be slow

# Executables inside each asset and the text their version output must contain.
_WANTED: dict[str, dict[str, tuple[list[str], str]]] = {
    "yt-dlp": {"yt-dlp.exe": (["--version"], "")},
    "ffmpeg": {"ffmpeg.exe": (["-version"], "ffmpeg version"),
               "ffprobe.exe": (["-version"], "ffprobe version")},
    "deno": {"deno.exe": (["--version"], "deno ")},
}

log = logging.getLogger("h190k.tool_import")


def _setup_logging() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            LOG_FILE.unlink()
        handler: logging.Handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()  # never write to stdout/stderr (windowed exe)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


def _parse_sha256(text: str, asset: str) -> str | None:
    """Same parsing rules as ``DependencyManager._expected_sha256``."""
    for line in text.splitlines():
        if asset in line:
            m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
            if m:
                return m.group(1).lower()
    hashes = re.findall(r"\b([0-9a-fA-F]{64})\b", text)
    return hashes[0].lower() if len(hashes) == 1 else None


def _expected_sha256(mgr: DependencyManager, tool: str, src: Path, online: bool) -> str | None:
    local = src / _CHECKSUM_ASSETS[tool]
    if local.is_file():
        try:
            expected = _parse_sha256(local.read_text("utf-8", "replace"), _ASSETS[tool])
        except OSError:
            expected = None
        if expected:
            log.info("%s: using checksum from %s", tool, local.name)
            return expected
    if not online:
        return None
    # The installer downloaded from releases/latest, so compare against the same release.
    base = f"https://github.com/{_REPOS[tool]}/releases/latest/download"
    expected = mgr._expected_sha256(tool, base)
    if expected:
        log.info("%s: using checksum fetched from %s", tool, base)
    return expected


def _smoke_test(exe: Path, args: list[str], must_contain: str) -> str:
    out = _run([str(exe), *args], timeout=_RUN_TIMEOUT).strip()
    if not out or (must_contain and must_contain not in out.lower()):
        raise DependencyError(f"{exe.name} did not run correctly (no version output).")
    first = out.splitlines()[0].strip()
    log.info("  %s -> %s", exe.name, first)
    return out


def _import_one(mgr: DependencyManager, tool: str, src: Path, online: bool) -> str | None:
    asset = src / _ASSETS[tool]
    log.info("%s: importing %s (%.1f MB)", tool, asset, asset.stat().st_size / 1048576)

    expected = _expected_sha256(mgr, tool, src, online)
    if expected:
        actual = mgr._sha256(asset)
        if actual != expected:
            raise DependencyError(f"{asset.name} failed checksum verification "
                                  f"(expected {expected}, got {actual}).")
        log.info("%s: SHA-256 OK", tool)
    else:
        log.warning("%s: published checksum unavailable; relying on the run check only", tool)

    mgr.tools_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{tool}-", dir=mgr.tools_dir))
    _ACTIVE_WORK.add(str(work))
    try:
        wanted = _WANTED[tool]
        staged: dict[str, Path] = {}
        if tool == "yt-dlp":
            target = work / "yt-dlp.exe"
            shutil.copyfile(asset, target)
            staged["yt-dlp.exe"] = target
        else:
            try:
                with zipfile.ZipFile(asset) as zf:
                    for info in zf.infolist():
                        name = info.filename.rsplit("/", 1)[-1].lower()
                        if name in wanted and name not in staged and not info.is_dir():
                            target = work / name
                            with zf.open(info) as fsrc, open(target, "wb") as fdst:
                                shutil.copyfileobj(fsrc, fdst, CHUNK)
                            staged[name] = target
            except zipfile.BadZipFile as exc:
                raise DependencyError(f"{asset.name} is corrupt.") from exc
            missing = [w for w in wanted if w not in staged]
            if missing:
                raise DependencyError(f"{', '.join(missing)} not found in {asset.name}.")

        # Run every executable from the staging folder before it replaces anything.
        outputs = {name: _smoke_test(path, *wanted[name]) for name, path in staged.items()}

        for name, path in staged.items():
            mgr._replace(path, mgr.tools_dir / name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _ACTIVE_WORK.discard(str(work))

    # Record the version exactly like DependencyManager._install().
    if tool == "ffmpeg":
        version: str | None = None
        if online:
            try:
                _tag, version = mgr._latest_release("ffmpeg")  # release date, as deps stores it
            except Exception:  # noqa: BLE001 - best effort
                version = None
        if version is None:  # offline / rate-limited: the build date from `ffmpeg -version`
            m = re.search(r"-(\d{4})(\d{2})(\d{2})\b", outputs.get("ffmpeg.exe", ""))
            version = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else time.strftime("%Y-%m-%d")
    else:
        # Same parsing as DependencyManager.installed_version(), reusing the smoke-test output
        # instead of starting the tool again.
        out = outputs.get(f"{tool}.exe", "")
        if tool == "yt-dlp":
            version = out.strip().splitlines()[0].strip() if out.strip() else None
        else:
            m = re.search(r"deno\s+(\d+\.\d+\.\d+)", out)
            version = m.group(1) if m else None
        version = version or mgr.installed_version(tool)
    mgr._write_version(tool, version)
    log.info("%s: installed %s", tool, version)
    return version


def import_tools(src_dir: str | Path, tools_dir: str | Path = TOOLS_DIR, online: bool = True) -> int:
    """Import the downloaded tool assets found in ``src_dir`` into ``tools_dir``.

    Returns a process exit code (0 ok, 1 some tool failed, 2 nothing to import / bad folder).
    One tool failing never blocks the others.
    """
    _setup_logging()
    src = Path(src_dir)
    log.info("=== import-tools from %s into %s", src, tools_dir)
    if not src.is_dir():
        log.error("source folder does not exist")
        return 2
    mgr = DependencyManager(Path(tools_dir))  # also cleans leftovers of interrupted installs
    provided = [t for t in TOOLS if (src / _ASSETS[t]).is_file()]
    if not provided:
        log.error("no tool assets found (expected any of: %s)", ", ".join(_ASSETS.values()))
        return 2
    failed: list[str] = []
    for tool in provided:
        try:
            _import_one(mgr, tool, src, online)
        except Exception:  # log everything, keep going
            log.exception("%s: FAILED", tool)
            failed.append(tool)
    versions = mgr._read_versions()
    log.info("result: %s", ", ".join(
        f"{t}={versions.get(t) if mgr._is_installed(t) else 'MISSING'}" for t in TOOLS))
    log.info("=== done (%s)", f"failed: {', '.join(failed)}" if failed else "ok")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    """``--import-tools <dir> [--offline]`` (argv excludes the flag itself)."""
    args = [a for a in argv if a != "--offline"]
    if len(args) != 1:
        _setup_logging()
        log.error("usage: --import-tools <folder> [--offline]; got %r", argv)
        return 2
    try:
        return import_tools(args[0], online="--offline" not in argv)
    except Exception:  # windowed exe: never let a traceback dialog appear
        _setup_logging()
        log.exception("unexpected error")
        return 1
