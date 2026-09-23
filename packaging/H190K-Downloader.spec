# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for H190K Downloader (onedir, windowed).
#
# Build with (from the repo root):
#   pyinstaller --noconfirm --clean packaging/H190K-Downloader.spec
# or just run scripts/build.ps1 / scripts/build.bat. All paths below are resolved relative to
# this spec file, so the working directory does not matter.
#
# The app does NOT bundle yt-dlp, ffmpeg or deno: it downloads the standalone
# tools into data/bin on first run and keeps them up to date itself.

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "H190K Downloader"
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # repo root
ICON = os.path.join(ROOT, "assets", "icon.ico")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # so collect_submodules can import core/ui

datas = [(ICON, "assets")]
datas += collect_data_files("customtkinter")  # themes (json) + fonts

hiddenimports = []
hiddenimports += collect_submodules("core")
hiddenimports += collect_submodules("ui")
hiddenimports += ["darkdetect", "PIL._tkinter_finder"]

excludes = [
    # Not used at runtime: we run the standalone yt-dlp.exe instead.
    "yt_dlp",
    "yt_dlp_ejs",
    # Pillow AVIF codec (~7.6 MB); thumbnails are JPEG/PNG/WebP.
    "PIL.AvifImagePlugin",
    "PIL._avif",
    # Big modules that sometimes get dragged in from the build environment.
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "pytest",
    "setuptools",
    "pkg_resources",
    "lib2to3",
    "pydoc_data",
    "test",
    "tkinter.test",
    "idlelib",
    "turtledemo",
    "sqlite3",
    "xmlrpc",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX increases antivirus false positives; keep off.
    console=False,      # windowed; core.cli attaches to the parent console for CLI flags
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
