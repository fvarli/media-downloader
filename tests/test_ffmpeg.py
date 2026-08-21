"""FFmpeg detection, including the explicit-location override."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader import ffmpeg as ffmpeg_module
from media_downloader.ffmpeg import FFmpegStatus, detect_ffmpeg


def test_detects_both_binaries_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda name, **_: f"/usr/bin/{name}")
    status = detect_ffmpeg()
    assert status.available
    assert status.ffmpeg == Path("/usr/bin/ffmpeg")
    assert status.location == Path("/usr/bin")


def test_reports_unavailable_when_nothing_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda name, **_: None)
    status = detect_ffmpeg()
    assert not status.available
    assert status.location is None


def test_partial_install_counts_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """yt-dlp needs ffprobe as well, so ffmpeg alone is not enough."""
    monkeypatch.setattr(
        ffmpeg_module.shutil,
        "which",
        lambda name, **_: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
    )
    assert not detect_ffmpeg().available


def test_explicit_directory_is_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        seen.append(path)
        return str(Path(path or "") / name)

    monkeypatch.setattr(ffmpeg_module.shutil, "which", fake_which)
    status = detect_ffmpeg(tmp_path)
    assert status.available
    assert seen == [str(tmp_path), str(tmp_path)]


def test_explicit_binary_path_uses_its_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp accepts either a directory or the binary itself."""
    binary = tmp_path / "bin" / "ffmpeg"
    binary.parent.mkdir()
    binary.write_text("")

    seen: list[str | None] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        seen.append(path)
        return str(Path(path or "") / name)

    monkeypatch.setattr(ffmpeg_module.shutil, "which", fake_which)
    detect_ffmpeg(binary)
    assert seen == [str(tmp_path / "bin"), str(tmp_path / "bin")]


def test_status_location_is_the_containing_directory() -> None:
    status = FFmpegStatus(ffmpeg=Path("/opt/tools/ffmpeg"), ffprobe=Path("/opt/tools/ffprobe"))
    assert status.location == Path("/opt/tools")
