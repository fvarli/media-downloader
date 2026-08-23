"""Per-user application directories.

A standalone build has no meaningful working directory -- a double-clicked
application on macOS starts in ``/`` -- so anything the application needs to
keep has to live in a location derived from the user's home directory, not from
wherever the process happened to start.

Resolution here is pure: these functions work out *where* a directory belongs
and never create it. Creation is an explicit, separate step, so importing this
module cannot leave directories behind on someone's disk.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from media_downloader.errors import OutputError
from media_downloader.web.system import current_platform

#: Directory name used on macOS and Windows, where user-visible application
#: folders are conventionally capitalised and spaced.
APP_DISPLAY_NAME = "Media Downloader"

#: Directory name used on Linux and anywhere else, matching XDG conventions.
APP_SLUG = "media-downloader"


def app_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the per-user directory for this application's own data.

    ==========  ====================================================
    macOS       ``~/Library/Application Support/Media Downloader``
    Windows     ``%LOCALAPPDATA%\\Media Downloader``
    Linux/other ``$XDG_DATA_HOME/media-downloader`` or
                ``~/.local/share/media-downloader``
    ==========  ====================================================

    The directory is *not* created. ``env`` is injectable so the platform
    rules can be tested without touching the real environment.
    """
    environ = os.environ if env is None else env
    platform = current_platform()
    home = _home(env)

    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DISPLAY_NAME

    if platform == "win32":
        # LOCALAPPDATA is set on every supported Windows version, but a
        # stripped service environment can lack it; fall back rather than fail.
        local = environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else home / "AppData" / "Local"
        return base / APP_DISPLAY_NAME

    xdg = environ.get("XDG_DATA_HOME", "").strip()
    # A relative XDG_DATA_HOME is invalid per the spec and is ignored.
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / APP_SLUG


def tools_dir(env: Mapping[str, str] | None = None) -> Path:
    """Directory holding optional tools this application manages itself.

    Everything under here is downloaded on the user's explicit request and
    belongs solely to this application: it is never added to ``PATH`` and never
    installed system-wide.
    """
    return app_data_dir(env) / "tools"


def logs_dir(env: Mapping[str, str] | None = None) -> Path:
    """Directory holding this application's own diagnostic log.

    A packaged application has no console, so a failure that would have been a
    line on a terminal has to land somewhere the user can find and send on.
    """
    return app_data_dir(env) / "logs"


def tool_install_dir(tool: str, version: str, env: Mapping[str, str] | None = None) -> Path:
    """Directory for one specific version of one managed tool.

    Versioning the directory means a new pinned version installs alongside the
    old one rather than overwriting a binary that may be in use.
    """
    return tools_dir(env) / tool / version


def ensure_dir(path: Path) -> Path:
    """Create ``path`` and its parents, and return it.

    The only function in this module with a side effect, so callers have to opt
    into creating anything.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"Could not create the directory '{path}': {exc}") from exc
    return path


def _home(env: Mapping[str, str] | None = None) -> Path:
    """The user's home directory, or the working directory if there is none.

    An injected environment is honoured here, not just by the callers that read
    XDG_DATA_HOME and LOCALAPPDATA directly. ``Path.home()`` reads the *process*
    environment, so a caller that passed HOME in a mapping got the real user's
    home anyway -- isolation that looks right and is not. That is how a CI check
    installed a managed tool into the runner's own Application Support
    directory: macOS derives its path from the home directory alone, and the
    injected HOME was never consulted.

    With no injected environment the behaviour is unchanged: ``Path.home()``
    already reads HOME on POSIX and the profile on Windows.
    """
    if env is not None:
        for key in ("HOME", "USERPROFILE"):
            value = env.get(key, "").strip()
            if value and Path(value).is_absolute():
                return Path(value)
    try:
        return Path.home()
    except (RuntimeError, OSError):  # pragma: no cover - home is normally resolvable
        return Path.cwd()
