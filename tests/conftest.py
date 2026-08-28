"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader.config import DownloadRequest
from media_downloader.ffmpeg import FFmpegStatus


@pytest.fixture(autouse=True)
def _isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give every test its own application-data root, before anything uses one.

    This exists because of a real defect. ``launcher.serve()`` configured file
    logging against the user's actual directory, and the tests that exercise it
    attached a RotatingFileHandler pointing at ``~/.local/share/...`` to the
    module-global logger. A handler outlives the call that created it, so from
    that moment every later test in the session appended to a real person's
    diagnostics log -- which then showed up in a support report they exported.

    Two things therefore have to be true, and patching a path only gives the
    first:

    * every per-user path resolves inside tmp_path;
    * no file handler survives into, or out of, a test.

    Redirecting HOME and the platform-specific variables covers each rule the
    application uses -- XDG_DATA_HOME on Linux, LOCALAPPDATA on Windows, and
    HOME on macOS via ~/Library/Application Support.
    """
    from media_downloader import diagnostics

    root = tmp_path / "app-data"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("XDG_DATA_HOME", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))

    # Start from a clean slate and leave one behind, so a handler opened by one
    # test can never be inherited by the next.
    diagnostics.remove_file_logging()
    diagnostics.STATE = diagnostics.DiagnosticsState()
    try:
        yield root
    finally:
        diagnostics.remove_file_logging()
        diagnostics.STATE = diagnostics.DiagnosticsState()


@pytest.fixture(autouse=True)
def _no_real_os_interaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a test would invoke real desktop UI.

    media_downloader.web.system only shells out for two things: opening a
    folder and showing the startup address. On macOS and Windows the latter is
    a modal dialog that blocks until a human clicks it, so an unpatched call
    does not fail on CI -- it hangs the job until it times out. Turning that
    into an immediate, obvious failure locally is worth the four lines.

    Tests that exercise those paths patch subprocess.run themselves; monkeypatch
    applies their patch after this one, so theirs wins.
    """
    from media_downloader.web import system

    class _RefusingSubprocess:
        """Stands in for the subprocess module inside web.system only.

        Replacing the name in that module's namespace, rather than setting an
        attribute on the shared subprocess module, matters: ``import
        subprocess`` binds one module object process-wide, so patching its
        ``run`` also breaks unrelated stdlib code -- on some Windows and Python
        combinations ``platform.machine()`` shells out to ``ver``.
        """

        SubprocessError = system.subprocess.SubprocessError
        CalledProcessError = system.subprocess.CalledProcessError

        @staticmethod
        def run(*args: object, **kwargs: object) -> None:
            pytest.fail(f"a test tried to launch a real process: {args!r}")

    monkeypatch.setattr(system, "subprocess", _RefusingSubprocess)


@pytest.fixture
def ffmpeg_present(tmp_path: Path) -> FFmpegStatus:
    return FFmpegStatus(ffmpeg=tmp_path / "ffmpeg", ffprobe=tmp_path / "ffprobe")


@pytest.fixture
def ffmpeg_absent() -> FFmpegStatus:
    return FFmpegStatus(ffmpeg=None, ffprobe=None)


@pytest.fixture
def request_factory(tmp_path: Path):
    def make(**overrides: Any) -> DownloadRequest:
        params: dict[str, Any] = {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "output_dir": tmp_path / "downloads",
        }
        params.update(overrides)
        return DownloadRequest(**params)

    return make


class FakeYoutubeDL:
    """Stand-in for ``yt_dlp.YoutubeDL`` used by the downloader tests."""

    def __init__(
        self,
        opts: dict[str, Any],
        *,
        info: dict[str, Any] | None = None,
        error: Exception | None = None,
        post_hook_path: str | None = None,
    ) -> None:
        self.opts = opts
        # yt-dlp exposes the options as `params`, and a postprocessor bound to
        # this object reads them to find FFmpeg. Same dict, both names.
        self.params = opts
        self._info = info
        self._error = error
        self._post_hook_path = post_hook_path
        self.closed = False
        self.calls: list[tuple[str, bool]] = []

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
        self.calls.append((url, download))
        if self._error is not None:
            raise self._error
        if download and self._post_hook_path is not None:
            for hook in self.opts.get("post_hooks", []):
                hook(self._post_hook_path)
        return self._info

    def close(self) -> None:
        self.closed = True
