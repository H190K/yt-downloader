# 🎬 YouTube Downloader Pro - GUI Edition

![Python](https://img.shields.io/badge/Python-3.7%2B-blue)
![GUI](https://img.shields.io/badge/GUI-Modern%20Tkinter-green)
![Cross-platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-orange)
![License](https://img.shields.io/badge/License-Open%20Source-red)

**Transform your YouTube videos into your personal media library with style!** 🚀

> **Note:** This is a better than average YouTube downloader with a nice interface and useful features.

---

## What's New

### User Interface
- **🟡 Light Mode**: Crisp, modern interface perfect for daytime coding sessions
- **🔵 Dark Mode**: Sleek, eye-friendly design for those late-night video marathons
- **🎨 Theme Consistency**: Every button, every menu, every color is perfectly harmonized
- **📱 Responsive Layout**: 800x700 window that adapts beautifully to content

### Sidebar Information

| 📥 **Download Guide** | Your personal video downloading tutorial |
|---------------------|----------------------------------------|
| Copy URL            | "Ctrl + V" ready for instant pasting 📋 |
| Fetch Details       | Hit Enter to analyze (or press the button) 🎯 |
| Select Format       | Choose your poison - MP4/MP3/M4A 🎬 |
| Download            | Ctrl + D for lightning-fast downloads ⚡ |

| 📁 **Format Info** | Quick reference at your fingertips |
|-------------------|-----------------------------------|
| MP4               | High-definition video with crystal-clear audio 🎥 |
| M4A               | Premium audio keeping original codec quality 🔊 |
| MP3               | Universal audio - plays anywhere 📱 |

| Features | Description |
|------------------------|---------------------|
| 🎯 **Smart Defaults** | Auto-saves to your system's Downloads folder |
| ☑️ **Zero Date Madness** | Clean filenames without timestamp clutter |
| 🔧 **Persistent Settings** | Remembers your last download location |
| 🎮 **Keyboard Mastery** | Full shortcut support for power users |
| 🖼️ **Thumbnail Preview** | Visual confirmation before downloading |
| 📊 **Real-time Progress** | Live updates with ETA estimation |

| Keyboard Shortcuts | Description |
|------------------------|------------------------|
| **Ctrl + V** | Paste URL instantly |
| **Enter** | Fetch video details |
| **Ctrl + D** | Start download |
| **F5** | Refresh app |

---

## Why This Downloader is Different

### Technical Details
1. **Multi-threaded Architecture**: Downloads run in separate threads - your GUI never freezes
2. **Memory Efficient**: Minimal resource usage, maximum performance
3. **Cross-platform Native**: Works seamlessly on Windows, macOS, and Linux
4. **FFmpeg Integration**: Ultra-fast processing with bundled `ffmpeg` binaries

### Developer Features
- **JSON Configuration**: Settings stored in `~/.youtube_downloader_config.json`
- **Thread-safe Queues**: Industry-standard inter-thread communication
- **Progress Hooks**: Real-time callback system
- **Error Handling**: Robust exception management

---

## Quick Start

### **Method 1: Run as Python Script** (Recommended for Developers)
```bash
# Clone or download the project
git clone https://github.com/H190K/yt-downloader.git
cd yt-downloader

# Install dependencies
pip install -r requirements.txt

# Launch the GUI
python app_threaded.py
```

### **Method 2: Standalone Executable** (Preferred for End Users)
1. **Download**: Grab `YouTubeDownloaderSetup.exe` from the releases section.
2. **Install**: Run the installer and follow the on-screen wizard.
3. **Enjoy**: Launch the application from your desktop or start menu.

---

## System Requirements

| **Requirement** | **Minimum** | **Recommended** |
|----------------|-------------|-----------------|
| Python Version | 3.7+ | 3.9+ |
| Memory | 512 MB | 2 GB |
| Storage | 100 MB | 500 MB |
| Internet | Any connection | Broadband for 4K |

---

## Installation Guide

### **For Development**
```bash
# 1. Clone
git clone https://github.com/H190K/yt-downloader.git

# 2. Dependencies
pip install customtkinter yt-dlp Pillow requests pyinstaller

# 3. Optional: Install system ffmpeg for MP3 support
# Windows: Included in ffmpeg/ folder
# macOS: brew install ffmpeg
# Linux: sudo apt install ffmpeg

# 4. Launch
python app_threaded.py
```

### **For End Users**
1. **Download** the latest release.
2. **Run** `YouTubeDownloaderSetup.exe` and follow the installation wizard.
3. **Launch** the application and start downloading instantly!

---

## File Structure

```
yt-downloader/
├── 🐍 app_threaded.py          # Main application
├── 📋 requirements.txt        # Python dependencies list
├── 🎨 icon.ico                # Application icon
├── 🎬 ffmpeg/                 # For media processing
│   ├── 📁 bin/                # Binary executables
│   ├── 📚 doc/                # FFmpeg documentation
│   ├── 🔧 include/            # Development headers
│   └── 📦 lib/                # FFmpeg libraries
├── 📖 README.md               # You're reading it! 😊
```

---

## Architecture Overview

### Multi-Threading
- **Main Thread**: GUI responsiveness (never frozen!)
- **Worker Thread**: Background downloads
- **Queue System**: Thread-safe progress updates
- **Callback Pattern**: Real-time UI updates

### **Download Pipeline**
```
1. User Input → Thread 1 (GUI)
2. URL Analysis → Thread 2 (Worker)
3. Quality Options → Thread 1 (Display)
4. Download Start → Thread 2 (Active)
5. Progress Updates → Both Threads (Synchronized)
6. Completion → Notification System
```

---

## Customization

### **Theme Customization**
- Easy to add new themes in `update_theme_colors()`
- Color schemes defined in JSON-friendly format
- Dynamic theme switching without restart

### **Feature Extensions**
- Add new formats by extending `_extract_formats`
- Custom naming schemes via `output_template`
- Integration-ready architecture for playlists/history

---

## Troubleshooting

### **Common Issues**

| **Problem** | **Solution** |
|-------------|--------------|
| *"yt-dlp not found"* | Install yt-dlp: `pip install yt-dlp` (Python module required, not CLI) |
| *"ffmpeg not found"* | Windows: Use bundled ffmpeg/macOS: `brew install ffmpeg/Linux: `sudo apt install ffmpeg` |
| *"Download failed"* | Check internet, try different format |
| *"GUI doesn't start"* | Verify Python 3.7+ and dependencies installed |
| *"No thumbnail shows"* | Ensure requests package is installed |

### **Debug Mode**
```python
# Enable detailed logging
python app_threaded.py --debug
```

---

## Tips

1. **Master the Shortcuts**: Become a command-line ninja
2. **Quality Selection**: 4K available if YouTube supports it
3. **Smart Pathing**: Downloads to system Downloads folder automatically
4. **Format Memory**: Remembers your last format choice
5. **Batch Processing**: Modify code for multiple URLs

---

## Future Plans

### **Phase 1: Power Features**
- [ ] Multiple simultaneous downloads
- [ ] Download queue with pause/resume
- [ ] Playlist support with progress tracking
- [ ] Download history with search

### **Phase 2: Media Library**
- [ ] File organization by artist/album
- [ ] Metadata auto-tagging
- [ ] Artist artwork auto-download
- [ ] Smart filename patterns

### **Phase 3: Advanced**
- [ ] Bandwidth limiting
- [ ] Scheduled downloads
- [ ] Watch later integration
- [ ] Real-time notifications

---

## Connect & Contribute

### **Created with ❤️ by H190K**

**🤝 Want to contribute?** This is open source - fork it, improve it, make it yours!

**📧 Get in Touch:**
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

[![Support via DeStream](https://img.shields.io/badge/🍕_Feed_the_Developer-DeStream-FF6B6B?style=for-the-badge)](https://destream.net/live/H190K/donate)

[![Crypto Donations](https://img.shields.io/badge/Crypto_Donations-NOWPayments-9B59B6?style=for-the-badge&logo=bitcoin&logoColor=colored)](https://nowpayments.io/donation?api_key=J0QACAH-BTH4F4F-QDXM4ZS-RCA58BH)

</div>

---

<div align="center">

**Built with ❤️ by [H190K](https://github.com/H190K)**


</div>
