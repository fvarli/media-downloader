"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader.config import DownloadRequest
from media_downloader.ffmpeg import FFmpegStatus


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
