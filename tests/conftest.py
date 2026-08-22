"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader.config import DownloadRequest
from media_downloader.ffmpeg import FFmpegStatus


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
