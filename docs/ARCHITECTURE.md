# Architecture

H190K Downloader is a Windows desktop app: a CustomTkinter GUI (`ui/`) on top of a small
backend (`core/`) that drives the standalone **yt-dlp.exe**, **ffmpeg.exe** and **deno.exe**.

```
main.py ──► ui.app.run_gui()          (no arguments)
        └─► core.cli.run_cli(argv)    (any argument: --update, --check, --setup, --download)

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
  from that thread. `cancel()` kills the whole process tree and removes `.part`/`.ytdl` leftovers.
- `friendly_error()` turns yt-dlp output into readable messages (login/cookies required, age
  restriction, geo-block, unsupported link, DRM, network).
- Vimeo page links are rewritten to `player.vimeo.com/video/<id>`, which works without login.

## UI (`ui/`)

- `app.py` — main window, sidebar navigation, toasts, shortcuts, first-run setup, background update
  check (throttled to once per 24 h).
- Pages: `download_page.py`, `queue_page.py` (max 2 concurrent downloads), `settings_page.py`,
  `about_page.py` (credits, links, tool versions and updates), `setup_screen.py`.
- `theme.py` (colors as light/dark pairs, fonts, icon), `widgets.py` (shared components),
  `util.py` (platform detection, quality labels, thumbnails, open folder).
- `backend.py` is the single import point for `core`; with `H190K_FAKE_BACKEND=1` it uses
  `_dev_fake.py`, a simulated backend for working on the UI without network access.

**Smoothness rules:** pages are built once and switched with `tkraise()`; worker threads post to a
thread-safe queue drained by the UI thread every 30 ms; progress is coalesced to ≤ 10 updates/s per
row and widgets are only reconfigured when a value changes; thumbnails are decoded off the UI thread;
the window stays hidden until built (no white flash).

## CLI (`core/cli.py`)

`--setup`, `--update`, `--check`, `--download URL [--mp3|--m4a] [--quality H] [--bitrate B]
[--playlist] [--cookies BROWSER] [--out DIR]`. The exe is windowed, so the CLI attaches to the parent
console (or opens one). Exit codes: 0 ok, 1 error, 2 bad arguments.

## Packaging

- `packaging/H190K-Downloader.spec` — PyInstaller onedir, windowed; `yt_dlp` and large unused
  modules are excluded; the icon is bundled under `assets/`.
- `packaging/installer.iss` — Inno Setup 6: MIT license page, default `C:\H190K Downloader`,
  no admin required, Start-menu links (website, GitHub, source, license, *Update tools*),
  optional tool download at the end of setup, removes `data\` on uninstall.
- `scripts/build.ps1` — builds both into `dist\`.
