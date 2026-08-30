# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone build.

Deliberately minimal. yt-dlp ships its own PyInstaller hook -- registered
through the ``pyinstaller40`` entry point, so PyInstaller finds it without being
told -- and that hook already collects the extractor modules, ``yt_dlp_ejs``
JavaScript, certificate bundles, pycryptodomex, websockets, requests, urllib3,
mutagen and brotli. Duplicating any of that here would only create a second
place to keep in sync.

We contribute three things yt-dlp cannot know about: our own web assets, the
licence texts that have to accompany a distributed binary, and the removal of
mutagen -- see the exclusion below.

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

import os
import re
import sys
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "media-downloader"

# Read from the package rather than repeated here: a version that has to be
# updated in two places eventually is not.
_init = Path(SPECPATH).parent / "src" / "media_downloader" / "__init__.py"
_match = re.search(r'__version__ = "([^"]+)"', _init.read_text(encoding="utf-8"))
assert _match, "could not read __version__ from the package"
APP_VERSION = _match.group(1)

# Console or windowed is an explicit build-time choice, not something to be
# rewritten later. Validation builds keep the console so CI can read --version,
# --help, exit codes and startup diagnostics directly; the eventual public
# macOS and Windows releases set MD_WINDOWED_BUILD=1 and rely on the file log
# and the native dialogs in media_downloader.web.system instead.
WINDOWED = os.environ.get("MD_WINDOWED_BUILD") == "1"
CONSOLE = not WINDOWED

# macOS wants a display name; the CLI keeps its lowercase invocation name.
BUNDLE_NAME = "Media Downloader"
IS_MACOS = sys.platform == "darwin"

# The web UI's HTML/CSS/JS are read through importlib.resources at runtime, so
# they must land inside the package directory in the bundle.
datas = collect_data_files("media_downloader", includes=["web/static/*"])

# A binary is a distribution, and several of the libraries inside it require
# their licence to accompany it -- certifi's MPL-2.0 most explicitly. Shipping
# the texts costs a few kilobytes and is the difference between complying and
# meaning to.
_repo_root = Path(SPECPATH).parent
datas += [
    (str(_repo_root / "LICENSE"), "."),
    (str(_repo_root / "THIRD-PARTY-NOTICES.md"), "."),
]

# Record console-vs-windowed *in* the bundle, because the application has to
# know at runtime and cannot reliably tell by looking. A double-clicked
# windowed build gets no arguments and has no console for a usage message, so
# this marker is what lets a bare launch open the interface instead of exiting
# silently. Read back by media_downloader.buildmode.
# A directory, because PyInstaller keeps the source filename: the marker has
# to be called build_mode.txt on disk to arrive under that name.
_marker_dir = Path(tempfile.mkdtemp(prefix="md-build-mode-"))
_marker = _marker_dir / "build_mode.txt"
_marker.write_text("windowed" if WINDOWED else "console", encoding="utf-8")
datas += [(str(_marker), "media_downloader")]

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Nothing here needs a GUI toolkit, a plotting stack or a test runner.
        "tkinter",
        "matplotlib",
        "numpy",
        "pytest",
        "PIL",
        # mutagen is GPL-2.0-or-later, and yt-dlp's own hook pulls it in as a
        # hidden import. It is optional there -- yt-dlp guards `import mutagen`
        # with try/except and only EmbedThumbnailPP uses it, which this
        # application never configures -- so excluding it removes copyleft code
        # from an MIT binary for a feature nobody can reach. The same reasoning
        # that made the managed FFmpeg an LGPL build.
        "mutagen",
    ],
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
    console=CONSOLE,
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

# A .app is a onedir bundle with a particular layout, so this wraps COLLECT
# rather than replacing it. Only built when windowed: a console .app would
# open a terminal window, which defeats the point.
if IS_MACOS and WINDOWED:
    app = BUNDLE(
        coll,
        name=f"{BUNDLE_NAME}.app",
        # TODO: an icon before the first public release.
        icon=None,
        # Permanent from here on. macOS identifies an application by this, not
        # by its name, so changing it later would make every installed copy
        # look like a different application. PyInstaller's default is the
        # display name -- "Media Downloader", with a space and no reverse-DNS
        # form -- which is not a valid identifier and would block notarisation.
        bundle_identifier="com.ferzendervarli.media-downloader",
        # Without this PyInstaller writes "0.0.0", which is what Finder's Get
        # Info showed for every build so far.
        version=APP_VERSION,
        info_plist={
            "CFBundleName": BUNDLE_NAME,
            "CFBundleDisplayName": BUNDLE_NAME,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            # No document types and no URL schemes: this application is opened
            # by the user, never by the system on their behalf.
            "LSBackgroundOnly": False,
        },
    )
