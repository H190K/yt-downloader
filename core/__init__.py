"""Backend core for H190K Downloader (paths, config, tool management, download engine, CLI).

This package is stdlib-only and never imports the ``yt_dlp`` Python module; it drives the
standalone ``yt-dlp.exe`` as a subprocess so that the tool can self-update.
"""

APP_NAME = "H190K Downloader"
__version__ = "2.0.0"
