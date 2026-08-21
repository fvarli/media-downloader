"""Configuration precedence: CLI flag > environment variable > default."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader.config import (
    ENV_FFMPEG_LOCATION,
    ENV_OUTPUT_DIR,
    build_request,
    default_output_dir,
    resolve_output_setting,
    resolve_setting,
)
from media_downloader.errors import OutputError


def test_cli_value_beats_environment() -> None:
    assert resolve_setting("cli", "VAR", {"VAR": "env"}) == "cli"


def test_environment_is_used_when_no_cli_value() -> None:
    assert resolve_setting(None, "VAR", {"VAR": "env"}) == "env"


def test_returns_none_when_neither_is_set() -> None:
    assert resolve_setting(None, "VAR", {}) is None


def test_blank_environment_value_is_treated_as_unset() -> None:
    assert resolve_setting(None, "VAR", {"VAR": "   "}) is None


def test_output_defaults_to_downloads_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_output_setting(None, {}) == (tmp_path / "downloads").resolve()
    assert default_output_dir().name == "downloads"


def test_output_environment_variable_is_honoured(tmp_path: Path) -> None:
    resolved = resolve_output_setting(None, {ENV_OUTPUT_DIR: str(tmp_path / "envdir")})
    assert resolved == (tmp_path / "envdir").resolve()


def test_output_flag_overrides_the_environment(tmp_path: Path) -> None:
    resolved = resolve_output_setting(
        str(tmp_path / "clidir"), {ENV_OUTPUT_DIR: str(tmp_path / "envdir")}
    )
    assert resolved == (tmp_path / "clidir").resolve()


def test_build_request_applies_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    request = build_request(url="https://example.com/v", env={})
    assert request.quality == "best"
    assert request.audio_only is False
    assert request.audio_format == "best"
    # None means no --filename was given, so automatic naming applies.
    assert request.filename_template is None
    assert request.ffmpeg_location is None
    assert request.output_dir == (tmp_path / "downloads").resolve()


def test_build_request_reads_the_ffmpeg_environment_variable(tmp_path: Path) -> None:
    request = build_request(
        url="https://example.com/v",
        env={ENV_FFMPEG_LOCATION: "/opt/ffmpeg/bin", ENV_OUTPUT_DIR: str(tmp_path)},
    )
    assert request.ffmpeg_location == "/opt/ffmpeg/bin"


def test_build_request_validates_the_filename_template(tmp_path: Path) -> None:
    with pytest.raises(OutputError):
        build_request(
            url="https://example.com/v",
            filename="../escape.mp4",
            env={ENV_OUTPUT_DIR: str(tmp_path)},
        )


@pytest.mark.parametrize(
    ("audio_only", "audio_format", "expected"),
    [
        (False, "best", False),
        (False, "mp3", False),
        (True, "best", False),
        (True, "mp3", True),
        (True, "flac", True),
    ],
)
def test_needs_audio_conversion(
    audio_only: bool, audio_format: str, expected: bool, tmp_path: Path
) -> None:
    """Only an explicit codec choice requires FFmpeg; 'best' keeps the source."""
    request = build_request(
        url="https://example.com/v",
        audio_only=audio_only,
        audio_format=audio_format,
        env={ENV_OUTPUT_DIR: str(tmp_path)},
    )
    assert request.needs_audio_conversion is expected
