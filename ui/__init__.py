"""customtkinter user interface for H190K Downloader.

Entry point: :func:`ui.app.run_gui`.

Set the environment variable ``H190K_FAKE_BACKEND=1`` to run the UI against the simulated
backend in :mod:`ui._dev_fake` (no network tools needed; useful for UI development).
"""

from core import __version__

APP_NAME = "H190K Downloader"
APP_VERSION = __version__  # single source of truth: core/__init__.py

AUTHOR = "H190K"
WEBSITE_URL = "https://h190k.com"
GITHUB_PROFILE_URL = "https://github.com/H190K"
REPO_URL = "https://github.com/H190K/yt-downloader"
ISSUES_URL = "https://github.com/H190K/yt-downloader/issues"
