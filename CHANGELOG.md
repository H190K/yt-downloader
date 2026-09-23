# Changelog

## 2.0.1 — 2026-09-23

### Fixed
- **Choosing 720p could give 4K.** The quality picked in the dropdown was reset to the Settings
  default when a link finished loading, when switching MP4/MP3/M4A, or when a setting changed. The
  explicit choice is now kept until you download or reset.
- **Every quality of a video had the same file name**, so a 720p download found the earlier 4K file
  and reported it as done. File names now end with the delivered quality.
- **"Failed" shown for a file that was actually downloaded.** Parallel downloads of the same video
  shared temporary and thumbnail files; a failed thumbnail/metadata step marked a finished download as
  failed; a late status update could flip a finished row to failed. Each download now has its own
  scratch folder, success is decided by checking the final file with ffprobe, and finished rows
  stay finished.
- **HTTP 403 from YouTube** (mostly with several downloads at once): automatic retries with fresh
  links, then another stream/player client at the same quality, then one step lower (clearly
  reported). If it still fails, the message explains why and what to try.
- Theme switching no longer shows a half-redrawn window; there is no white flash at startup.
- "Not available in your country" errors are recognised; YouTube Music links are detected;
  cookie error wording.

### Added
- **Quality in the file name**: `Title [id] - 720p.mp4`, `- 2160p.mp4`, `- 320kbps.mp3`,
  `- 128kbps.m4a`. Downloading the same quality again reuses the existing file ("Already in your
  download folder").
- **Smooth theme cross-fade** and a title bar that follows the theme.
- **Installer downloads the tools itself** on a "Downloading required tools" page with progress,
  Retry / Skip and no console window (`/DOWNLOADTOOLS` for silent installs). The command-line
  options (`--setup`, `--update`, `--check`) remain for advanced users.
- Queue rows show the requested and delivered quality, warnings, full error messages and
  "Open Settings" for login errors; identical downloads are not queued twice.
- Automated tests (`tests/`): offline unit tests and a real-download stress test.

## 2.0.0 — 2026-09-23

A full rewrite.

### Added
- **Multi-site downloads**: YouTube, Instagram, TikTok, Facebook, X/Twitter, SoundCloud, Vimeo,
  Reddit and everything else yt-dlp supports.
- **Self-contained tools**: on first run the app downloads yt-dlp, FFmpeg and Deno into its own
  `data\bin` folder. No Python needed on the target PC.
- **Updates**: *About & updates → Check for updates / Update all*, an optional startup check,
  the `--update` command and a Start-menu *Update H190K Downloader tools* shortcut.
- **Download queue** with per-item progress, speed/ETA, cancel, retry, show file / open folder
  (2 downloads at a time).
- **Playlists**, **MP3 bitrate choice** (320/256/192/128 kbps), **embedded thumbnail & metadata**.
- **Cookies from browser** for Instagram, private and age-restricted content.
- **Command line**: `--setup`, `--update`, `--check`, `--download`.
- **New interface**: sidebar navigation, preview card, dark / light / system themes, first-run
  setup screen, About page with credits and links.
- **Windows installer** (Inno Setup): MIT license page, default install folder `C:\H190K Downloader`,
  no admin rights needed, Start-menu links, optional tool download during setup.
- MIT `LICENSE`, `docs/ARCHITECTURE.md`.

### Fixed
- The selected quality is now delivered exactly (4K gives 2160p). Video and audio streams are always
  merged, so MP4s have sound at every resolution.
- Site-specific extractors are used again. The old `force_generic_extractor` option broke most sites.
- MP4s play in the default Windows player: non-AAC audio is converted to AAC and HEVC to H.264.

### Removed
- The bundled `ffmpeg/` folder and the `yt_dlp` Python dependency (replaced by the auto-installed tools).
- `app_threaded.py` (replaced by `main.py` + `core/` + `ui/`).
