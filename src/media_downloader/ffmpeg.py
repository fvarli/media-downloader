"""FFmpeg discovery and the guidance shown when it is missing.

FFmpeg is never installed automatically. When it is absent the application
either degrades gracefully (plain video downloads fall back to progressive
streams) or fails with a clear explanation, depending on what was asked for.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

FFMPEG_GUIDANCE = (
    "FFmpeg was not found. It is a separate program, not a Python package, and "
    "must be installed system-wide and be reachable on your PATH. Install it "
    "with your usual package manager (for example apt, dnf, pacman, Homebrew, "
    "winget, Chocolatey or Scoop), or download a build from https://ffmpeg.org/download.html "
    "and point this tool at it with --ffmpeg-location."
)


@dataclass(frozen=True)
class FFmpegStatus:
    """Where FFmpeg and FFprobe were found, if anywhere."""

    ffmpeg: Path | None
    ffprobe: Path | None

    @property
    def available(self) -> bool:
        """True when both binaries are present.

        yt-dlp needs ffprobe as well as ffmpeg for stream inspection, so a
        partial installation counts as unavailable.
        """
        return self.ffmpeg is not None and self.ffprobe is not None

    @property
    def location(self) -> Path | None:
        """Directory to hand to yt-dlp as ``ffmpeg_location``."""
        return self.ffmpeg.parent if self.ffmpeg is not None else None


def _find_in_dir(directory: Path, name: str) -> Path | None:
    """Look for ``name`` inside ``directory`` honouring Windows extensions."""
    found = shutil.which(name, path=str(directory))
    return Path(found) if found else None


def detect_ffmpeg(explicit_location: str | Path | None = None) -> FFmpegStatus:
    """Locate ffmpeg and ffprobe.

    ``explicit_location`` may be either the directory holding the binaries or
    the path to the ffmpeg binary itself, matching yt-dlp's own convention.
    When it is ``None`` the system PATH is searched; :func:`shutil.which`
    applies ``PATHEXT`` on Windows, so ``ffmpeg.exe`` is found there without
    any platform-specific code.
    """
    if explicit_location is not None:
        location = Path(explicit_location).expanduser()
        directory = location if location.is_dir() else location.parent
        return FFmpegStatus(
            ffmpeg=_find_in_dir(directory, "ffmpeg"),
            ffprobe=_find_in_dir(directory, "ffprobe"),
        )

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return FFmpegStatus(ffmpeg=Path(ffmpeg), ffprobe=Path(ffprobe))

    # Nothing usable on PATH: fall back to a copy the user asked us to install.
    # This is a pure lookup -- it never downloads anything.
    managed = _managed_ffmpeg_dir()
    if managed is not None:
        return FFmpegStatus(
            ffmpeg=_find_in_dir(managed, "ffmpeg"),
            ffprobe=_find_in_dir(managed, "ffprobe"),
        )

    return FFmpegStatus(
        ffmpeg=Path(ffmpeg) if ffmpeg else None,
        ffprobe=Path(ffprobe) if ffprobe else None,
    )


def _managed_ffmpeg_dir() -> Path | None:
    """Directory of an installed managed FFmpeg, if there is a complete one.

    Imported lazily to keep this module free of an import cycle: the tools
    package depends on paths, which depends on the platform seam.
    """
    try:
        from media_downloader.tools.manager import ToolManager
        from media_downloader.tools.manifest import FFMPEG

        return ToolManager().managed_dir(FFMPEG)
    except Exception:  # pragma: no cover - discovery must never break startup
        return None
