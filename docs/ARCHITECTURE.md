# Architecture

H190K Downloader is a Windows desktop app: a CustomTkinter GUI (`ui/`) on top of a small
backend (`core/`) that drives the standalone **yt-dlp.exe**, **ffmpeg.exe** and **deno.exe**.

```
main.py ──► ui.app.run_gui()          (no arguments)
        ├─► core.tool_import          (--import-tools DIR: installer only, no console)
        └─► core.cli.run_cli(argv)    (any other argument: --update, --check, --setup, --download)

ui/  ──uses──►  core.deps.DependencyManager   installs / updates the tools
               core.engine.fetch_info()       yt-dlp -J  -> MediaInfo
               core.engine.DownloadJob        yt-dlp download + ffmpeg post-processing
               core.config                    data/config.json
               core.paths                     where everything lives
```

## Why standalone tools instead of the `yt_dlp` Python module

The shipped app is a PyInstaller build. A Python module frozen into the exe can never be updated,
but YouTube and other sites change often and yt-dlp ships fixes every few weeks. Running the official
`yt-dlp.exe` as a subprocess lets the app replace it at any time (**Check for updates**, or
`--update`), and the target PC needs no Python at all.

## Folders (`core/paths.py`)

| Name | Frozen exe | From source |
|------|-----------|-------------|
| `APP_DIR` | folder of `H190K Downloader.exe` | repo root |
| `DATA_DIR` | `APP_DIR\data` if writable, else `%LOCALAPPDATA%\H190K Downloader` | same rule |
| `TOOLS_DIR` | `DATA_DIR\bin` (yt-dlp, ffmpeg, ffprobe, deno, `versions.json`) | |
| `CONFIG_FILE` | `DATA_DIR\config.json` | |
| `RESOURCE_DIR` | PyInstaller `_MEIPASS` (bundled `assets/icon.ico`) | repo root |

The installer defaults to `C:\H190K Downloader` and grants Users *modify* on `{app}\data`,
so the tools folder is writable without admin rights.

## Tools (`core/deps.py`)

| Tool | Source | Version |
|------|--------|---------|
| yt-dlp | `yt-dlp/yt-dlp` latest release `yt-dlp.exe` | release tag |
| ffmpeg + ffprobe | `yt-dlp/FFmpeg-Builds` `ffmpeg-master-latest-win64-gpl.zip` | build date; update offered when ≥ 30 days older (`FFMPEG_UPDATE_MIN_AGE_DAYS`) |
| deno | `denoland/deno` `deno-x86_64-pc-windows-msvc.zip` | release tag |

- Latest versions come from the GitHub API; when rate-limited it falls back to reading the
  `releases/latest` redirect.
- Downloads stream with progress, retry transient errors, verify the published SHA-256 when available
  and are swapped in atomically (`os.replace`). One tool failing never blocks the others.
- Leftover temp folders/files from an interrupted install are cleaned up on start.
- `core/tool_import.py` installs files the **installer** already downloaded (`--import-tools DIR`):
  verifies SHA-256, extracts, runs each tool once, moves it into place and records versions exactly
  like `deps` does. It never opens a console and logs to `data\import-tools.log`.

## Engine (`core/engine.py`)

**Fetching info** runs `yt-dlp -J` and builds a `MediaInfo`. `heights` lists the real video
resolutions (the smaller side of each format, so a 1080x1920 Short is `1080`). For playlists the
first entry is probed so the quality list is real.

**Downloading** builds the command in `build_download_args()`:

| Kind | Format selection |
|------|------------------|
| MP4 | `-f "bv*+ba/b" -S "res:<H>,fps,vcodec:h264,acodec:aac,ext:mp4:m4a" --merge-output-format mp4` |
| MP3 | `-f "ba/b" -x --audio-format mp3 --audio-quality <bitrate>K` |
| M4A | `-f "ba[acodec^=mp4a]/ba[ext=m4a]/ba/b" -x --audio-format m4a` (AAC copied when possible) |

- **Resolution comes first** in the sort, so choosing 4K yields 2160p; codec preference
  (H.264 > VP9 > AV1) only breaks ties at the same resolution. Video-only and audio-only streams are
  always merged, so every MP4 has sound. (Combined "progressive" streams on YouTube are 360p only —
  the old app's `best[height<=H]` approach silently fell back to them.)
- After each MP4, `ffprobe` checks the streams: non-AAC/MP3/ALAC audio → AAC 192k, HEVC video → H.264,
  so files play in the default Windows player.
- Every call passes `--ffmpeg-location`, `--js-runtimes deno:<path>`, `--ignore-config`,
  `--encoding utf-8`, `--no-mtime`, `--windows-filenames` and a machine-parsable `--progress-template`.
  The final path is captured with `--print after_move:filepath`.
- `DownloadJob` runs in its own thread and reports `on_progress(job, dict)` / `on_done(job, ok, message)`
  from that thread. `cancel()` kills the whole process tree and removes the job's scratch folder.
- `diagnose_error()` / `friendly_error()` turn yt-dlp output into a `(kind, message)` pair
  (`login_required`, `age_restricted`, `members_only`, `private`, `http_403`, `unsupported_url`,
  `network`, `geo_blocked`, `drm`, `unavailable`, `error`).
- Vimeo page links are rewritten to `player.vimeo.com/video/<id>`, which works without login.

**File names** carry the delivered quality so several qualities of one video can coexist:
`<title> [<id>] - 720p.mp4`, `- 2160p.mp4` (for "best" too — the label is the real resolution, the
smaller side of width × height, so a 1080×1920 Short is `1080p`), `- 320kbps.mp3`, `- 128kbps.m4a`
(probed bitrate). The file is downloaded under the requested label and renamed after the `ffprobe`
check. Same delivered quality = same file: a second job that would produce an existing valid file
reuses it (`already_downloaded=True`, "Already in your download folder") instead of overwriting it;
a module-level registry makes sure one job never replaces a file another active job owns.

**Isolation:** every job uses `-P home:<out_dir> -P temp:<out_dir>\.h190k-tmp\<job id>`, so parallel
jobs for the same video never share `.part`, `.fNNN` or thumbnail files. The scratch folder is removed
on success, failure and cancel; stale ones (> 3 days) are cleaned up.

**Success rule:** a job succeeds when the final file exists, is non-empty and `ffprobe` shows the
expected streams — not by yt-dlp's exit code. If only a later step failed (e.g. thumbnail embedding),
the complete file is still delivered, with `job.warning`. A broken file is deleted, never reported.

**HTTP 403:** YouTube occasionally refuses a freshly signed stream URL, mostly when several downloads
run at once. The engine retries up to 5 times with backoff — first with a fresh extraction (new URLs),
then excluding the refused format and alternating the player client (default / `web_embedded`),
which may step down one resolution; that is reported via `delivered_quality` and `job.warning`
("4K isn't downloadable … Saved in 1440p instead."). Only then does it fail with an `http_403`
message that explains the cause and the options (another quality, update tools, try later /
another network, browser cookies).

Job attributes the UI reads: `state`, `output_path`, `delivered_quality`, `warning`,
`already_downloaded`, `error_kind`, `suggested_quality` (reserved, currently always `None`).

## UI (`ui/`)

- `app.py` — main window, sidebar navigation, toasts, shortcuts, first-run setup, background update
  check (throttled to once per 24 h).
- Pages: `download_page.py`, `queue_page.py` (max 2 concurrent downloads), `settings_page.py`,
  `about_page.py` (credits, links, tool versions and updates), `setup_screen.py`.
- `theme.py` (colors as light/dark pairs, fonts, icon), `widgets.py` (shared components),
  `util.py` (platform detection, quality labels, thumbnails, open folder).
- `backend.py` is the single import point for `core`; with `H190K_FAKE_BACKEND=1` it uses
  `_dev_fake.py`, a simulated backend for working on the UI without network access.
- The download page remembers the user's explicit format/quality choice (a finished fetch, switching
  tiles or changing Settings defaults never overrides it). The queue refuses an exact duplicate
  (same video + format + quality) and each row shows the requested and delivered quality, warnings,
  and actions (Retry, Open Settings for login errors, Show file / Open folder).

**Smoothness rules:** pages are built once and switched with `tkraise()`; worker threads post to a
thread-safe queue drained by the UI thread every 30 ms; progress is coalesced to ≤ 10 updates/s per
row and widgets are only reconfigured when a value changes; thumbnails are decoded off the UI thread;
the window is cloaked at the compositor level until fully painted (no white flash at startup).

**Theme switching** (`ui/transition.py`): customtkinter repaints widget by widget, which showed a
half-light/half-dark window. Now every theme change goes through `App.apply_theme()`: the client area
is captured with `PrintWindow`, shown in a click-through overlay, the theme is switched underneath
with redraws batched, and the overlay fades out over 220 ms (cubic ease-out). The Windows title bar
follows the theme (DWM immersive dark mode), rapid switches are coalesced, a watchdog removes the
overlay if anything goes wrong, and "System" follows the Windows setting. Never call
`ctk.set_appearance_mode()` directly.

## CLI (`core/cli.py`)

`--setup`, `--update`, `--check`, `--download URL [--mp3|--m4a] [--quality H] [--bitrate B]
[--playlist] [--cookies BROWSER] [--out DIR]`. The exe is windowed, so the CLI attaches to the parent
console (or opens one). Exit codes: 0 ok, 1 error, 2 bad arguments. `--import-tools` is
installer-only and handled in `main.py` before the CLI so it never touches a console.

## Packaging

- `packaging/H190K-Downloader.spec` — PyInstaller onedir, windowed; `yt_dlp` and large unused
  modules are excluded; the icon is bundled under `assets/`.
- `packaging/installer.iss` — Inno Setup 6: MIT license page, default `C:\H190K Downloader`,
  no admin required, Start-menu links (website, GitHub, source, license, *Update tools*), removes
  `data\` on uninstall. The *Download required tools now* task (on by default) downloads yt-dlp,
  FFmpeg and Deno on a native "Downloading required tools" page, then runs
  `--import-tools {tmp}` hidden ("Installing required tools"). Failures offer Retry / Skip and never
  fail the install; the app's first-run screen is the fallback. `/VERYSILENT` skips the download
  unless `/DOWNLOADTOOLS` is given. Test overrides: `/DYtdlpBase`, `/DFfmpegBase`, `/DDenoBase`,
  `/DAppName`, `/DAppIdValue`.
- `scripts/build.ps1` — builds both into `dist\`.

## Tests (`tests/`)

- `tests/unit/` — fast, offline: command-line building, file naming, URL normalisation, error
  mapping, quality helpers, config and tool-version logic. `python -m pytest tests/unit -q`
- `tests/e2e/` — real downloads with the installed tools (skipped unless `--run-network`): every
  MP4 quality, MP3 bitrates and M4A of one video concurrently, cancel isolation and the CLI, each
  verified with `ffprobe`. `python -m pytest tests -q --run-network`

See `tests/README.md` for details.
