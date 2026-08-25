"""Entry point for the frozen application.

PyInstaller needs a module to start from, and it must not be the development
shim in ``main.py``: that one manipulates ``sys.path`` relative to ``__file__``,
which has no meaning inside a bundle.

This module exists to make sure a packaged build can never fail without saying
so. A windowed build has no console, so anything printed is discarded -- which
is how a Windows double-click that exited with a usage error looked exactly
like nothing happening at all. Two things prevent a repeat: the log file is
opened before any work is attempted, and a failure that would otherwise be
invisible is put on screen in a dialog.
"""

from __future__ import annotations

import multiprocessing
import sys


def _report_invisible_failure(exc: BaseException | None, code: int) -> None:
    """Put a failure in front of a user who has no console to read.

    Best effort throughout: a build that is already failing must not fail
    differently because reporting the failure went wrong.
    """
    try:
        from media_downloader.buildmode import is_windowed_app

        if not is_windowed_app():
            return
        # Only a launch that carried no arguments. That is the double-click,
        # and the only case where a failure is genuinely invisible. Anyone who
        # passed arguments to a windowed build is working from a command line
        # and has chosen where output goes; interrupting them with a modal
        # dialog for an ordinary "that is not a URL" would be wrong, and on an
        # unattended machine it blocks until somebody clicks it -- which is
        # exactly how this hung two CI jobs for two minutes each.
        if len(sys.argv) > 1:
            return

        from media_downloader.diagnostics import STATE, record_error
        from media_downloader.logging_setup import get_logger
        from media_downloader.web.system import show_startup_error

        logger = get_logger("entry")
        if exc is not None:
            error_id = record_error(logger, exc, context="startup failed")
            message = str(getattr(exc, "message", None) or exc) or type(exc).__name__
        else:
            error_id = record_error(
                logger,
                RuntimeError(f"the application exited with status {code}"),
                context="startup failed",
            )
            message = "The application stopped before its interface opened."

        log_file = STATE.log_file
        show_startup_error(message, error_id, log_file.parent if log_file else None)
    except Exception:
        pass


def _main() -> int:
    # Diagnostics first, and before the arguments are even looked at. A failure
    # in parsing used to leave no trace at all, which made a real Windows
    # startup bug undiagnosable from the user's side.
    try:
        from media_downloader.buildmode import build_mode, is_frozen
        from media_downloader.diagnostics import configure_file_logging
        from media_downloader.logging_setup import get_logger

        configure_file_logging()
        # One line, so that even an exit before anything else happens leaves a
        # trace. Records the *shape* of the invocation rather than the
        # arguments: a URL can carry credentials in its query string, and the
        # count is what answers "was this a double-click?".
        get_logger("entry").info(
            "launch mode=%s frozen=%s args=%d",
            build_mode(),
            is_frozen(),
            len(sys.argv) - 1,
        )
    except Exception:
        pass

    from media_downloader.cli import main

    return main()


if __name__ == "__main__":
    # Required before anything else on frozen builds: without it, any child
    # process re-runs the whole application instead of the worker function.
    multiprocessing.freeze_support()

    try:
        status = _main()
    except SystemExit as exit_request:
        status = int(exit_request.code or 0)
        if status != 0:
            _report_invisible_failure(None, status)
    except BaseException as exc:
        _report_invisible_failure(exc, 1)
        status = 1
    else:
        if status != 0:
            _report_invisible_failure(None, status)

    sys.exit(status)
