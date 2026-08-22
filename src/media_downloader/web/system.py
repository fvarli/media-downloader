"""Operating-system integration for the web UI.

Three narrow jobs: pick a sensible download directory, open that directory in
the desktop file manager, and open a browser. Each is isolated here so it can
be tested with the platform call monkeypatched.

This module holds the project's only process invocations. They pass argument
*lists*, never a shell string, and never a value that came from the browser.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from media_downloader.errors import OutputError
from media_downloader.logging_setup import get_logger

logger = get_logger("web.system")

#: Folder created inside the user's Downloads directory. Keeping downloads in
#: their own folder means "Open folder" lands somewhere recognisably ours
#: instead of among every file the browser has ever fetched.
APP_FOLDER_NAME = "Media Downloader"


def default_download_dir() -> Path:
    """Choose the directory the web UI downloads into.

    Prefers ``~/Downloads/Media Downloader``. If the home directory or its
    Downloads folder cannot be determined, falls back through ``~/Downloads``,
    then ``~``, then a project-relative ``downloads`` folder, so this never
    raises and never returns a path outside the user's control.

    Uses :meth:`pathlib.Path.home`, so it is correct on Linux, macOS and
    Windows without any hardcoded path.
    """
    try:
        home = Path.home()
    except (RuntimeError, OSError):  # pragma: no cover - home is normally resolvable
        logger.debug("Home directory could not be resolved; using the working directory.")
        return Path.cwd() / "downloads"

    downloads = home / "Downloads"
    if downloads.is_dir():
        return downloads / APP_FOLDER_NAME
    if home.is_dir():
        return home / APP_FOLDER_NAME
    return Path.cwd() / "downloads"  # pragma: no cover - home exists in practice


def open_folder(directory: Path) -> None:
    """Open ``directory`` in the desktop file manager.

    The caller supplies a directory the server already owns; no path from the
    browser ever reaches here. The command is always an argument list with no
    shell involved, so nothing in the path can be interpreted as syntax.

    Raises:
        OutputError: if the path is not an existing directory, or the platform
            handler is unavailable.
    """
    target = Path(directory).expanduser().resolve()
    if not target.is_dir():
        raise OutputError(f"The download folder does not exist yet: {target}")

    try:
        if sys.platform == "win32":
            # startfile takes a path, not a command line; nothing is parsed.
            os.startfile(target)  # type: ignore[attr-defined]
            return
        command = ["open" if sys.platform == "darwin" else "xdg-open", str(target)]
        # No shell=True: the path is passed as a single argv entry.
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise OutputError(
            "No file manager could be opened on this system.",
            hint=f"The downloads are in: {target}",
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise OutputError(
            f"The download folder could not be opened: {exc}",
            hint=f"The downloads are in: {target}",
        ) from exc


def open_browser(url: str) -> bool:
    """Open ``url`` in the user's default browser.

    Returns ``True`` when a browser was launched. Failure is not an error:
    headless machines, WSL and SSH sessions legitimately have no browser, and
    the caller prints the URL as a fallback either way.
    """
    try:
        return bool(webbrowser.open(url))
    except Exception:  # pragma: no cover - webbrowser is defensive already
        logger.debug("Could not launch a browser for %s", url)
        return False
