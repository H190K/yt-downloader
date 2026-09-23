# Changelog

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
