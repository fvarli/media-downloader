"""How this copy of the application was built, and therefore how it was launched.

A windowed build has no console. That is decided when the bundle is produced,
so it is recorded then rather than guessed at runtime: a marker file written by
the PyInstaller spec, read back through the same ``importlib.resources``
mechanism the web assets already use.

Guessing was tempting and would have been wrong. ``sys.stdout is None`` is true
for a windowed Windows build but not for a macOS ``.app`` launched from Finder,
and "is stdout a terminal?" answers a different question than "was this built
without a console". A build-time fact deserves a build-time answer.

Why any of this matters: a double-clicked application receives no command-line
arguments, and there is no console for a usage message to appear in. Knowing
the build is windowed is what lets a bare launch mean "open the interface"
instead of failing silently.
"""

from __future__ import annotations

import sys
from importlib.resources import files

#: Written into the bundle by packaging/media-downloader.spec.
MARKER_NAME = "build_mode.txt"

WINDOWED = "windowed"
CONSOLE = "console"


def is_frozen() -> bool:
    """True when running from a packaged build."""
    return bool(getattr(sys, "frozen", False))


def build_mode() -> str:
    """``"windowed"`` or ``"console"``.

    Defaults to console whenever the marker cannot be read -- running from
    source, an older bundle, or a resource lookup that fails. Console is the
    safe default: it preserves the ordinary command-line contract, and the only
    thing a wrong answer there costs is a usage message somebody can see.
    """
    try:
        marker = files("media_downloader").joinpath(MARKER_NAME)
        recorded = marker.read_text(encoding="utf-8").strip().lower()
    except (OSError, ModuleNotFoundError, AttributeError):
        return CONSOLE
    return WINDOWED if recorded == WINDOWED else CONSOLE


def is_windowed_app() -> bool:
    """True when this is a packaged build with no console attached.

    Both halves are required. A source checkout is never windowed however it
    was started, and a frozen console build still has somewhere to print.
    """
    return is_frozen() and build_mode() == WINDOWED
