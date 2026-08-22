# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone build.

Deliberately minimal. yt-dlp ships its own PyInstaller hook -- registered
through the ``pyinstaller40`` entry point, so PyInstaller finds it without being
told -- and that hook already collects the extractor modules, ``yt_dlp_ejs``
JavaScript, certificate bundles, pycryptodomex, websockets, requests, urllib3,
mutagen and brotli. Duplicating any of that here would only create a second
place to keep in sync.

We contribute exactly one thing yt-dlp cannot know about: our own web assets.

onedir, not onefile: onefile re-extracts the whole bundle to a temporary
directory on every launch, which is slow with yt-dlp's footprint and trips
antivirus heuristics far more often. onedir is also what a macOS .app actually
is, and it is the layout yt-dlp's own updater recognises.

FFmpeg and Deno are deliberately NOT bundled. They are fetched later, on the
user's explicit request, verified against a pinned checksum -- see
media_downloader.tools.

Build with:
    .venv/bin/python -m PyInstaller packaging/media-downloader.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "media-downloader"

# The web UI's HTML/CSS/JS are read through importlib.resources at runtime, so
# they must land inside the package directory in the bundle.
datas = collect_data_files("media_downloader", includes=["web/static/*"])

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here needs a GUI toolkit, a plotting stack or a test runner.
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "PIL"],
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on Linux: the URL fallback and CLI output both want a terminal.
    # Windowed macOS/Windows builds come in a later phase, where
    # media_downloader.web.system.report_startup_url takes over.
    console=True,
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
