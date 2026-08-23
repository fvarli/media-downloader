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

#: How long to leave the startup-address dialog up. Long enough for someone
#: who just launched the app, short enough that it cannot hang forever.
DIALOG_TIMEOUT_SECONDS = 300


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


def current_platform() -> str:
    """Return the platform name.

    Indirection on purpose. It gives the tests a clean seam to exercise every
    platform's branch instead of patching a stdlib module attribute, and it
    stops type checking from being platform-dependent: comparing ``sys.platform``
    directly makes a type checker treat the other branches as unreachable on
    whichever OS it happens to be running on.
    """
    return sys.platform


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

    platform = current_platform()
    try:
        if platform == "win32":
            # os.startfile exists only on Windows, so it is fetched dynamically.
            # It takes a path, not a command line: nothing is parsed as syntax.
            # getattr, not os.startfile: the attribute does not exist off
            # Windows, so direct access fails type checking on Linux and macOS.
            startfile = getattr(os, "startfile")  # noqa: B009
            startfile(target)
            return
        command = ["open" if platform == "darwin" else "xdg-open", str(target)]
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


def report_startup_url(url: str) -> None:
    """Show the local address when a browser could not be opened for it.

    A packaged application has no console, so the address printed at startup
    would go nowhere. Rather than leave the user staring at nothing, put it
    somewhere they will actually see.
    """
    _show_dialog(f"Media Downloader is running.\n\nOpen this address in your browser:\n{url}")


def show_startup_error(message: str, error_id: str, log_dir: Path | None = None) -> None:
    """Tell the user the application could not start at all.

    Some failures happen before the interface exists: the data directory cannot
    be created, the port will not bind, bundled assets are missing. In a
    packaged build there is no console left to print to, so this is the only
    channel. It carries a short human-readable reason, the error ID they can
    quote, and where the logs are -- never a traceback.
    """
    lines = ["Media Downloader could not start.", "", message, "", f"Error ID: {error_id}"]
    if log_dir is not None:
        lines += ["", "Diagnostic logs:", str(log_dir)]
    _show_dialog("\n".join(lines))


def _show_dialog(message: str) -> None:
    """Put ``message`` in front of the user by whatever means the OS offers.

    Best effort by design: failing to show a dialog must never stop a server
    that is already running, nor mask the error it was trying to report.
    """
    platform = current_platform()
    try:
        if platform == "win32":
            import ctypes

            # getattr for the same reason as os.startfile: windll does not
            # exist off Windows, so direct access fails type checking on Linux
            # and macOS. MB_OK | MB_ICONINFORMATION; a message box takes no
            # command line, so nothing in the text is parsed as syntax.
            windll = getattr(ctypes, "windll")  # noqa: B009
            windll.user32.MessageBoxW(None, message, "Media Downloader", 0x40)
            return
        if platform == "darwin":
            script = (
                f"display dialog {_applescript_string(message)} "
                'with title "Media Downloader" buttons {"OK"} default button "OK"'
            )
            # argv list, never a shell string. Bounded: this can run on a
            # background thread, and a dialog nobody is there to dismiss must
            # not pin a subprocess for the life of the process.
            subprocess.run(["osascript", "-e", script], check=False, timeout=DIALOG_TIMEOUT_SECONDS)
            return
    except Exception:  # pragma: no cover - a dialog must never be fatal
        logger.debug("Could not display a dialog.")
    # Linux, and the fallback everywhere else, is the console we already have.
    print(message, file=sys.stderr)


def _applescript_string(value: str) -> str:
    """Quote a string for AppleScript, escaping backslashes and quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def open_log_folder(env: dict[str, str] | None = None) -> None:
    """Open the application's own log directory.

    Takes no path from anywhere: the directory is derived from the same
    per-user rules the rest of the application uses, exactly like Open
    Downloads Folder.
    """
    from media_downloader.paths import ensure_dir, logs_dir

    open_folder(ensure_dir(logs_dir(env)))


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
