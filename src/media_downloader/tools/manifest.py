"""Pinned sources for the tools this application can install.

Every download the application performs is described here and nowhere else: a
URL that is not in this table can never be fetched. Changing what gets
downloaded therefore means changing a reviewable file, which is the point.

Each checksum below was taken from the vendor's own published checksum file,
not computed locally and never invented. A platform with no verified source yet
is simply absent, and the application reports it as unsupported rather than
guessing a URL or a hash.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: Tool identifiers used in the API and on disk.
FFMPEG = "ffmpeg"
DENO = "deno"

TOOL_NAMES: tuple[str, ...] = (FFMPEG, DENO)


@dataclass(frozen=True)
class ToolSpec:
    """One pinned download for one tool on one platform and architecture."""

    tool: str
    version: str
    url: str
    sha256: str
    size_bytes: int
    #: Logical executable name -> path inside the archive. A trailing path is
    #: matched against the end of the archive member, so a version-stamped top
    #: level directory does not have to be pinned.
    members: Mapping[str, str]
    licence: str
    source: str

    @property
    def executables(self) -> tuple[str, ...]:
        """Logical names that must exist before an install is accepted."""
        return tuple(self.members)


# Keyed by (tool, sys.platform value, machine architecture).
#
# Only linux/x86_64 is configured. macOS and Windows entries are deliberately
# absent until a real source and checksum have been verified for them; the
# application reports "not configured" for those rather than pretending.
_MANIFEST: dict[tuple[str, str, str], ToolSpec] = {
    (FFMPEG, "linux", "x86_64"): ToolSpec(
        tool=FFMPEG,
        version="n9.0.1",
        url=(
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "autobuild-2026-08-21-13-40/"
            "ffmpeg-n9.0.1-6-g9d4ca21220-linux64-lgpl-9.0.tar.xz"
        ),
        sha256="3cbb7c9994adcc071d2e88632e6020fbb5206f1d881b071cca33dffed710370e",
        size_bytes=113_400_000,
        members=MappingProxyType({"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"}),
        # The LGPL variant is chosen over the GPL one deliberately: nothing we
        # do needs the GPL-only components, and the static build avoids any
        # runtime shared-library resolution.
        licence="LGPL-2.1-or-later",
        source="https://github.com/BtbN/FFmpeg-Builds",
    ),
    (DENO, "linux", "x86_64"): ToolSpec(
        tool=DENO,
        version="2.9.5",
        url=(
            "https://github.com/denoland/deno/releases/download/"
            "v2.9.5/deno-x86_64-unknown-linux-gnu.zip"
        ),
        sha256="8b010a3b1a4a0188a67cdb8a7a27348b2a501af78aec7fc74f2ace167368d530",
        size_bytes=41_600_000,
        members=MappingProxyType({"deno": "deno"}),
        licence="MIT",
        source="https://github.com/denoland/deno",
    ),
}


def normalise_arch(machine: str) -> str:
    """Map ``platform.machine()`` spellings onto the names used above."""
    value = machine.lower()
    if value in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value


def normalise_platform(platform: str) -> str:
    """Map ``sys.platform`` values onto the names used above."""
    if platform.startswith("linux"):
        return "linux"
    if platform == "darwin":
        return "macos"
    if platform == "win32":
        return "windows"
    return platform


def lookup(tool: str, platform: str, machine: str) -> ToolSpec | None:
    """Return the pinned spec for this tool here, or ``None`` if unconfigured.

    ``None`` is a normal answer, not an error: it means we have no verified
    source for this combination yet.
    """
    return _MANIFEST.get((tool, normalise_platform(platform), normalise_arch(machine)))


def available_tools() -> tuple[str, ...]:
    """Every tool the application knows how to install, on some platform."""
    return TOOL_NAMES
