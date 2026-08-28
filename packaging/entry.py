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
import os
import sys

#: Enough to prove the handshake, the redirect and a real transfer, without
#: pulling down a whole tool on every CI job.
PROBE_BYTES = 512 * 1024


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

    # Internal validation only, like MD_DIAGNOSTIC_SELFTEST and MD_NO_BROWSER:
    # never documented, never offered, off unless the variable is set. It exists
    # so CI can put a real incompatible file through the *packaged* application
    # -- its bundled code, its FFmpeg discovery -- rather than through the
    # source checkout, which proves nothing about what was shipped.
    fixture = os.environ.get("MD_COMPAT_SELFTEST")
    if fixture:
        return _compatibility_selftest(fixture)

    probe = os.environ.get("MD_TLS_SELFTEST")
    if probe:
        return _tls_selftest(probe)

    from media_downloader.cli import main

    return main()


def _tls_selftest(url: str) -> int:
    """Prove the packaged build's HTTPS trust, through the shared path.

    Internal validation only, like the other self-tests: never documented,
    never offered, off unless the variable is set.

    It reports which trust sources loaded *and* performs a real verified
    request, because those catch different things. A real machine with a
    complete certificate store downloads successfully either way -- which is
    exactly how a Windows build that could not install anything shipped green.
    The trust sources are the part that does not depend on the machine.
    """
    import tempfile
    from pathlib import Path

    from media_downloader.tools.trust import https_fetch, trust_sources

    sources = trust_sources()
    print(f"https trust: {sources.describe()}")
    print(f"authorities: {sources.authority_count}")
    print(f"certifi: {sources.certifi}")
    if not sources.certifi:
        print("TLS self-test FAILED: certifi was not loaded")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "probe.bin"
        try:
            # A bounded prefix of the real pinned asset: enough to complete
            # the handshake, follow the redirect and actually move bytes, but
            # not the whole tool. Exceeding the limit is the expected outcome.
            https_fetch(url, target, max_bytes=PROBE_BYTES)
        except Exception as exc:  # the failure is the result
            message = str(getattr(exc, "message", None) or exc)
            if "larger than expected" not in message:
                print(f"TLS self-test FAILED: {message}")
                return 1
        transferred = target.stat().st_size if target.exists() else 0
        if not transferred:
            print("TLS self-test FAILED: the connection carried no data")
            return 1
        print(f"verified request to the pinned host succeeded ({transferred} bytes read)")
    print("TLS self-test passed")
    return 0


def _compatibility_selftest(fixture: str) -> int:
    """Normalise one local file through the packaged pipeline and report."""
    from pathlib import Path

    from media_downloader.compatibility import MediaProbe, validate_universal
    from media_downloader.normalize import make_universal_postprocessor

    target = Path(fixture)
    processor = make_universal_postprocessor()

    before = validate_universal(MediaProbe.from_ffprobe(processor.get_metadata_object(str(target))))
    print(f"before: ok={before.ok} {before.as_log_fields()}")

    _, info = processor.run({"filepath": str(target)})
    produced = Path(info["filepath"])
    after = validate_universal(
        MediaProbe.from_ffprobe(processor.get_metadata_object(str(produced)))
    )
    print(f"after:  ok={after.ok} {after.as_log_fields()}")
    if not after.ok:
        print("compatibility self-test FAILED: " + "; ".join(after.problems))
        return 1
    print("compatibility self-test passed")
    return 0


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
