"""Tests must never touch the real user's application data.

This file exists because they did. Running the web server inside the suite
configured file logging against ~/.local/share/media-downloader/logs/ and
attached a RotatingFileHandler to the module-global logger. A handler outlives
the call that created it, so from that point every later test appended to a
real person's diagnostics log -- 120 of the 285 records in it turned out to be
pytest debris, which then appeared in a support report they exported.

These tests fail if that can happen again.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from media_downloader import diagnostics, paths
from media_downloader.logging_setup import LOGGER_NAME

#: Captured at import time, before any fixture redirects Path.home(), so these
#: are the directories a real user's data would genuinely live in.
_REAL_HOME = Path.home()
REAL_APP_DATA = (
    _REAL_HOME / ".local" / "share" / "media-downloader",
    _REAL_HOME / "Library" / "Application Support" / "Media Downloader",
    _REAL_HOME / "AppData" / "Local" / "Media Downloader",
)


# -- the paths themselves -----------------------------------------------


def test_app_data_never_resolves_to_the_real_user_directory(
    _isolated_app_data: Path,
) -> None:
    """The core guarantee: with no env passed, nothing points at a real home."""
    resolved = paths.app_data_dir()
    assert _isolated_app_data in resolved.parents or resolved == _isolated_app_data
    for real in REAL_APP_DATA:
        assert real != resolved
        assert real not in resolved.parents


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_every_platform_rule_stays_inside_the_isolated_root(
    platform: str, _isolated_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each OS derives the directory differently; all three must be contained."""
    monkeypatch.setattr(paths, "current_platform", lambda: platform)
    for resolved in (paths.app_data_dir(), paths.tools_dir(), paths.logs_dir()):
        assert _isolated_app_data in resolved.parents or resolved == _isolated_app_data


def test_the_log_directory_is_inside_the_isolated_root(_isolated_app_data: Path) -> None:
    assert _isolated_app_data in paths.logs_dir().parents


# -- handler lifecycle ---------------------------------------------------


def test_no_file_handler_leaks_into_a_test() -> None:
    """A handler from an earlier test would still hold its original file open."""
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert handlers == []


def test_removing_file_logging_closes_the_file(_isolated_app_data: Path) -> None:
    path = diagnostics.configure_file_logging()
    assert path is not None and path.is_file()

    diagnostics.remove_file_logging()
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert handlers == []
    assert diagnostics.STATE.log_file is None


def test_configuring_twice_does_not_hold_two_files_open(_isolated_app_data: Path) -> None:
    diagnostics.configure_file_logging()
    diagnostics.configure_file_logging()
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1


# -- the exact path that caused the defect -------------------------------


def test_writing_a_log_lands_only_inside_the_isolated_root(
    _isolated_app_data: Path,
) -> None:
    from media_downloader.logging_setup import get_logger

    path = diagnostics.configure_file_logging()
    assert path is not None
    get_logger("t").info("a record from the test suite")
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()

    assert _isolated_app_data in path.parents
    assert "a record from the test suite" in path.read_text()


def test_running_the_server_does_not_touch_a_real_log(
    _isolated_app_data: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """launcher.serve() is precisely what corrupted the user's log.

    Its file logging must resolve inside the isolated root, so exercising the
    server in tests can never append to real diagnostics again.
    """
    import io

    from rich.console import Console

    from media_downloader.web import launcher

    class StubServer:
        def __init__(self, config: object) -> None:
            self.url = "http://127.0.0.1:9999"

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(launcher, "WebServer", StubServer)
    monkeypatch.setattr(launcher, "open_browser", lambda url: True)

    launcher.serve(
        Console(file=io.StringIO()),
        download_dir=tmp_path / "dl",
        open_browser_on_start=False,
    )

    log = diagnostics.STATE.log_file
    assert log is not None
    assert _isolated_app_data in log.parents
    for real in REAL_APP_DATA:
        assert real not in log.parents
