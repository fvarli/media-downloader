"""Output directory resolution and output-template validation.

Filename *sanitisation* is deliberately left to yt-dlp (see
:mod:`media_downloader.options`, which enables ``windowsfilenames`` on every
platform). This module only makes sure a user-supplied template cannot escape
the chosen output directory.
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from media_downloader.errors import OutputError

DEFAULT_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
DEFAULT_AUDIO_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")


def resolve_output_dir(raw: str | Path) -> Path:
    """Expand and absolutise an output directory without creating it.

    ``~`` is expanded via :meth:`pathlib.Path.expanduser`, which works on
    Linux, macOS and Windows alike.
    """
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise OutputError(f"Invalid output directory '{raw}': {exc}") from exc


def ensure_output_dir(path: Path) -> Path:
    """Create ``path`` (including parents) and confirm it is a writable dir."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(
            f"Could not create the output directory '{path}': {exc}",
            hint="Choose a different location with --output.",
        ) from exc

    if not path.is_dir():
        raise OutputError(f"The output path '{path}' exists but is not a directory.")

    return path


def validate_filename_template(template: str) -> str:
    """Validate a yt-dlp output template supplied via ``--filename``.

    The template must name a file *inside* the output directory, so path
    separators, absolute paths, Windows drive letters, UNC prefixes and
    parent-directory hops are all rejected. Rules are applied identically on
    every OS, so a template that works on Linux also works on Windows.

    Raises:
        OutputError: if the template could escape the output directory.
    """
    candidate = template.strip()
    if not candidate:
        raise OutputError("The filename template is empty.")

    if "\x00" in candidate or any(ord(char) < 0x20 for char in candidate):
        raise OutputError("The filename template contains control characters.")

    if _DRIVE_OR_UNC.match(candidate):
        raise OutputError(
            f"The filename template '{candidate}' must be relative, not an absolute path.",
            hint="Use --output to choose the directory and --filename for the name only.",
        )

    # Check both separator conventions regardless of host OS.
    if "/" in candidate or "\\" in candidate:
        raise OutputError(
            f"The filename template '{candidate}' must not contain path separators.",
            hint="Use --output to choose the directory and --filename for the name only.",
        )

    if candidate in {".", ".."} or PureWindowsPath(candidate).is_absolute():
        raise OutputError(f"The filename template '{candidate}' is not a valid file name.")

    return candidate
