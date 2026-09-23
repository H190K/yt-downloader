# 🎬 H190K Downloader

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue)
![GUI](https://img.shields.io/badge/GUI-CustomTkinter-green)
![Engine](https://img.shields.io/badge/Engine-yt--dlp-orange)
![License](https://img.shields.io/badge/License-MIT-red)

**Download videos and audio from YouTube, Instagram, TikTok and many more sites - in one clean desktop app.** 🚀

> The installer is self-contained: **no Python needed** on your PC. On first launch the app downloads the tools it needs and keeps them up to date by itself.

---

## Features

| Feature | Description |
|---------|-------------|
| 🌍 **Multi-site** | YouTube, Instagram, TikTok, Facebook, X/Twitter, SoundCloud, Vimeo, Reddit and [everything else yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) |
| 🎥 **MP4 with audio** | The best video stream and the best audio stream are downloaded separately and merged into one MP4 with FFmpeg - audio is always included, at every resolution |
| 🎵 **MP3 / M4A** | Audio extraction - MP3 at 320/256/192/128 kbps, or M4A keeping the original quality |
| 📐 **Exact quality** | The quality list shows only the resolutions the video really has, and the file you get is exactly the one you picked (4K means 2160p, not a silent fallback) |
| 📋 **Download queue** | Queue several downloads, each with its own progress bar, cancel button and "open folder" |
| 📚 **Playlists** | Download a whole playlist when the link is one |
| 🖼️ **Preview** | Thumbnail, title, uploader, duration and site before you download |
| 🏷️ **Metadata** | Embedded thumbnail and metadata in the output files |
| 🍪 **Browser cookies** | Use your browser login (Firefox recommended) for Instagram, private or age-restricted content |
| 🔄 **Self-updating tools** | yt-dlp, FFmpeg and Deno are installed automatically and updated from inside the app |
| 🌗 **Themes** | Dark, light or follow the system |

### About quality & formats

- **Up to 1080p** the MP4 uses H.264 video + AAC audio - plays everywhere.
- **1440p / 4K** on YouTube only exist as VP9 (or AV1) video, so those MP4s are VP9/AV1 + AAC. They play in the Windows 11 *Media Player* and VLC; older players may need the free *VP9 Video Extensions* from the Microsoft Store.
- Non-AAC audio (e.g. Opus) is converted to AAC, and HEVC video (common on TikTok) is converted to H.264, so every MP4 has sound and plays in the default Windows player.
- Vertical videos (Shorts, Reels, TikTok) are listed by their short side, so a 1080x1920 Short shows as **1080p**.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Ctrl + V** | Paste URL |
| **Enter** | Fetch video details |
| **Ctrl + D** | Start download |
| **F5** | Reset the form |

---

## Quick Start

### **For End Users** (no Python required)
1. **Download** `H190K-Downloader-Setup-<version>.exe` from the [releases](https://github.com/H190K/yt-downloader/releases) page.
2. **Install**: run the setup. It installs to `C:\H190K Downloader` by default (you can change it) and does not need administrator rights.
3. **First launch**: the app downloads yt-dlp, FFmpeg and Deno (about 170 MB download, ~430 MB on disk) into its `data\bin` folder. Leave the installer's *"Download required tools now"* box ticked to do this during setup.
4. **Paste a link, pick MP4 / MP3 / M4A, download.** Files are saved to `Downloads\H190K Downloader` by default (you can change this in Settings).

### **For Developers** (run from source)
```bat
git clone https://github.com/H190K/yt-downloader.git
cd yt-downloader
scripts\run.bat
```
`scripts\run.bat` creates a `.venv`, installs `requirements.txt` and starts `python main.py`.
Requires Python 3.10+ (3.12 recommended) on Windows.

---

## Tools & Updates

The app does not bundle any download tools. It fetches the official standalone builds:

| Tool | Used for | Source |
|------|----------|--------|
| **yt-dlp.exe** | Downloading from all supported sites | [yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases) |
| **ffmpeg.exe / ffprobe.exe** | Merging video + audio, MP3/M4A conversion, thumbnails | [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds/releases) |
| **deno.exe** | JavaScript runtime yt-dlp needs for YouTube | [Deno releases](https://github.com/denoland/deno/releases) |

They are stored in `<install folder>\data\bin`. If the install folder is not writable, `%LOCALAPPDATA%\H190K Downloader\bin` is used instead.

**Updating** (sites change often - update when downloads start failing):
- In the app: **About & updates -> Check for updates** (enable *Check for tool updates on startup* in Settings to do it automatically).
- Start menu: **Update H190K Downloader tools**.
- Command line: `"H190K Downloader.exe" --update` (or `scripts\update.bat` in the source tree).

---

## Command Line

The same exe works as a command-line tool when given arguments:

| Command | Description |
|---------|-------------|
| `--setup` | Install any missing tools |
| `--update` | Install missing tools and update all tools to the latest version |
| `--check` | Show installed tools and their versions |
| `--download URL` | Download without opening the GUI |
| `--download URL --mp3` / `--m4a` | Download audio only |
| `--download URL --quality 1080` | Limit video resolution |
| `--download URL --out DIR` | Save to a specific folder |
| `--download URL --mp3 --bitrate 192` | MP3 bitrate (320 / 256 / 192 / 128) |
| `--download URL --playlist` | Download the whole playlist |
| `--download URL --cookies firefox` | Use cookies from a browser (chrome / edge / firefox / brave / opera) |

```bat
"C:\H190K Downloader\H190K Downloader.exe" --check
"C:\H190K Downloader\H190K Downloader.exe" --download "https://youtu.be/..." --mp3 --out "D:\Music"
python main.py --update        &rem from source
```

---

## Instagram, Private & Age-restricted Content

Some sites (Instagram in particular) only allow downloads when you are logged in.
Log in to the site in your normal browser, then in **Settings -> Sign-in & cookies -> Use cookies from browser** choose that browser
(Chrome, Edge, Firefox, Brave or Opera). The app passes your browser cookies to yt-dlp; nothing is uploaded anywhere.

> Tip: **Firefox works best.** Current Chrome / Edge / Brave versions on Windows encrypt their cookies in a way other programs can't read ("app-bound encryption"), so loading cookies from them often fails - log in to the site in Firefox and choose Firefox here.

---

## Building the EXE and Installer

Requirements: Windows 10/11 x64, Python 3.10+ and (optional, for the installer) [Inno Setup 6](https://jrsoftware.org/isdl.php).

```bat
scripts\build.bat
```
or
```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-Version 2.1.0] [-NoInstaller] [-Recreate]
```

`scripts\build.ps1`:
1. creates / reuses an isolated `.venv-build` and installs `requirements-dev.txt` (includes PyInstaller),
2. runs PyInstaller with `packaging\H190K-Downloader.spec` -> `dist\H190K Downloader\H190K Downloader.exe` (one-folder, windowed),
3. if Inno Setup's `ISCC.exe` is found, compiles `packaging\installer.iss` -> `dist\H190K-Downloader-Setup-<version>.exe`.

The installer:
- shows the MIT license, installs per-user without admin rights (default `C:\H190K Downloader`, changeable),
- creates a Start menu folder (app, *Update H190K Downloader tools*, website / GitHub / source-code links, license, uninstall) and an optional desktop shortcut,
- can download the required tools right after installing,
- removes the downloaded tools and settings (`data\`) on uninstall.

Silent install: `H190K-Downloader-Setup-<version>.exe /VERYSILENT /CURRENTUSER [/DIR="D:\Apps\H190K Downloader"]`

---

## File Structure

```
yt-downloader/
├── main.py                    # Entry point: GUI, or CLI when arguments are given
├── core/                      # Backend (stdlib only, no GUI code)
│   ├── paths.py               # App / data / tools folder locations
│   ├── config.py              # Settings (data/config.json)
│   ├── deps.py                # Installs & updates yt-dlp, FFmpeg, Deno
│   ├── engine.py              # Fetch info + download jobs (runs yt-dlp.exe)
│   └── cli.py                 # --update / --check / --setup / --download
├── ui/                        # CustomTkinter interface
│   ├── app.py                 # Main window, navigation, shortcuts, first-run setup
│   ├── download_page.py, queue_page.py, settings_page.py, about_page.py, setup_screen.py
│   ├── widgets.py, theme.py, util.py, backend.py
│   └── _dev_fake.py           # Simulated backend for UI work (H190K_FAKE_BACKEND=1)
├── assets/
│   └── icon.ico               # Application icon
├── packaging/
│   ├── H190K-Downloader.spec  # PyInstaller spec (onedir, windowed)
│   ├── installer.iss          # Inno Setup 6 script
│   ├── make_wizard_images.py  # Regenerates the installer images from the icon
│   └── assets/                # Installer wizard images
├── scripts/
│   ├── run.bat                # Run from source (creates .venv)
│   ├── build.ps1 / build.bat  # Build exe + installer into dist/
│   └── update.bat             # Update tools (exe or source)
├── docs/
│   └── ARCHITECTURE.md        # How the pieces fit together
├── requirements.txt           # Runtime dependencies
├── requirements-dev.txt       # + PyInstaller
├── CHANGELOG.md
├── LICENSE                    # MIT
└── README.md                  # You're reading it! 😊
```

Runtime data (not in git): `data/bin/` (tools, `versions.json`), `data/cache/` and `data/config.json`.

---

## Troubleshooting

| **Problem** | **Solution** |
|-------------|--------------|
| *Tools failed to download* | Check your internet connection / firewall, then run **Update H190K Downloader tools** or `--setup` |
| *A site suddenly stopped working* | Run **About & updates -> Check for updates** - sites change and yt-dlp is updated often |
| *Instagram / private video fails* | Choose your browser under **Settings -> Sign-in & cookies** and make sure you are logged in there |
| *YouTube asks to "sign in to confirm you're not a bot"* | Use browser cookies as above, and make sure the tools are up to date |
| *Video plays without sound* | Pick **MP4** (it always merges audio); update the tools if it persists |
| *Antivirus warning* | PyInstaller apps are sometimes falsely flagged; build from source with `scripts\build.bat` if in doubt |

---

## License

[MIT](LICENSE) © H190K. The downloaded tools keep their own licenses: yt-dlp (Unlicense), FFmpeg (GPL v3), Deno (MIT).

Please only download content you have the right to download, and respect each site's terms of service.

---

## Connect & Contribute

### **Created with ❤️ by H190K**

**🤝 Want to contribute?** This is open source - fork it, improve it, make it yours!

**📧 Get in Touch:**
- 🌐 **Website**: [h190k.com](https://h190k.com)
- 🐙 **GitHub**: [@H190K](https://github.com/H190K) - source code: [H190K/yt-downloader](https://github.com/H190K/yt-downloader)
- 🐦 **Twitter/X**: [@h190k](https://twitter.com/h190k) - Follow for updates
- 📧 **Email**: [info@h190k.com](mailto:info@h190k.com) - For business inquiries
- 🌟 **Star** this repo if it helped you!

## 💖 Support the Project

Love this worker? Here's how you can help:

- 🍴 **Fork it** and add your own features
- 🐛 **Report bugs** or suggest improvements via [GitHub Issues](https://github.com/H190K/yt-downloader/issues)
- 📢 **Share it** with developers who You think might need this
- ⭐ **Star the repo** to show your support

If my projects make your life easier, consider buying me a coffee! Your support helps me create more open-source tools for the community.

<div align="center">

[![Fiat Donation](https://img.shields.io/badge/💵_Fiat_Donation-H190K/Sindipay-ff7a18?style=for-the-badge&logo=creditcard&logoColor=white)](https://donation.h190k.com/)

[![Crypto Donations](https://img.shields.io/badge/Crypto_Donations-NOWPayments-9B59B6?style=for-the-badge&logo=bitcoin&logoColor=colored)](https://nowpayments.io/donation?api_key=J0QACAH-BTH4F4F-QDXM4ZS-RCA58BH)

</div>

---

<div align="center">

**Built with ❤️ by [H190K](https://github.com/H190K)**


</div>
