"""CLI parsing, output and exit codes. No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader import cli as cli_module
from media_downloader.cli import build_parser, run
from media_downloader.config import ENV_OUTPUT_DIR
from media_downloader.downloader import DownloadResult, MediaInfo
from media_downloader.errors import DownloadFailedError, ExitCode, MediaUnavailableError

SAMPLE_MEDIA_INFO = MediaInfo(
    title="Example Video",
    uploader="Example Channel",
    duration_seconds=213,
    extractor="Youtube",
    webpage_url="https://www.youtube.com/watch?v=abc",
    width=1920,
    height=1080,
    filesize_bytes=12_345_678,
    ext="mp4",
)

VALID_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def flat(text: str) -> str:
    """Collapse all whitespace so assertions survive console re-wrapping.

    Rich wraps output at the console width, which differs between platforms and
    CI runners. Matching a mid-sentence phrase against the raw capture would
    test the layout rather than the message.
    """
    return " ".join(text.split())


class StubDownloader:
    """Replaces the real Downloader so no network call is ever made."""

    def __init__(self, result_path: Path | None = None, error: Exception | None = None) -> None:
        self.result_path = result_path
        self.error = error
        self.requests: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> StubDownloader:
        return self

    def fetch_info(self, request: Any) -> MediaInfo:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SAMPLE_MEDIA_INFO

    def download(self, request: Any) -> DownloadResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result_path is not None
        return DownloadResult(path=self.result_path, info=SAMPLE_MEDIA_INFO)


@pytest.fixture
def stub_downloader(monkeypatch: pytest.MonkeyPatch):
    def install(result_path: Path | None = None, error: Exception | None = None) -> StubDownloader:
        stub = StubDownloader(result_path=result_path, error=error)
        monkeypatch.setattr(cli_module, "Downloader", stub)
        return stub

    return install


# -- argument parsing ---------------------------------------------------


def test_url_is_required() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([])
    assert excinfo.value.code == int(ExitCode.USAGE_ERROR)


def test_defaults_match_the_documented_behaviour() -> None:
    args = build_parser().parse_args([VALID_URL])
    assert args.url == VALID_URL
    assert args.quality == "best"
    assert args.audio is False
    assert args.audio_format == "best"
    assert args.info is False
    assert args.output is None


def test_all_documented_options_parse() -> None:
    args = build_parser().parse_args(
        [
            VALID_URL,
            "--audio",
            "--audio-format",
            "mp3",
            "--quality",
            "1080",
            "--output",
            "/tmp/x",
            "--filename",
            "%(title)s.%(ext)s",
            "--ffmpeg-location",
            "/opt/ffmpeg",
            "--overwrite",
        ]
    )
    assert args.audio is True
    assert args.audio_format == "mp3"
    assert args.quality == "1080"
    assert args.filename == "%(title)s.%(ext)s"
    assert args.overwrite is True


def test_verbose_and_quiet_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([VALID_URL, "--verbose", "--quiet"])


def test_invalid_quality_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([VALID_URL, "--quality", "8k"])


def test_help_mentions_every_documented_example() -> None:
    help_text = build_parser().format_help()
    for fragment in ("--audio", "--quality", "--output", "--filename", "--info"):
        assert fragment in help_text


# -- exit codes ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x"],
)
def test_bad_urls_exit_with_the_invalid_url_code(url: str) -> None:
    assert run([url]) == int(ExitCode.INVALID_URL)


def test_filename_escaping_the_output_dir_exits_with_the_output_code(tmp_path: Path) -> None:
    code = run([VALID_URL, "--filename", "../escape.mp4", "--output", str(tmp_path)])
    assert code == int(ExitCode.OUTPUT_ERROR)


def test_successful_download_returns_zero(
    stub_downloader: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    final = tmp_path / "Example Video [abc].mp4"
    stub_downloader(result_path=final)
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.SUCCESS)
    assert str(final) in capsys.readouterr().out


def test_the_final_path_is_printed_alone_on_stdout(
    stub_downloader: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Everything else goes to stderr so the path can be piped."""
    final = tmp_path / "video.mp4"
    stub_downloader(result_path=final)
    run([VALID_URL, "--output", str(tmp_path), "--quiet"])
    assert capsys.readouterr().out.strip() == str(final)


def test_unavailable_media_exits_with_its_own_code(stub_downloader: Any, tmp_path: Path) -> None:
    stub_downloader(error=MediaUnavailableError("private video"))
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.MEDIA_UNAVAILABLE)


def test_download_failure_exits_with_its_own_code(stub_downloader: Any, tmp_path: Path) -> None:
    stub_downloader(error=DownloadFailedError("network down"))
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.DOWNLOAD_FAILED)


def test_interrupt_exits_with_130(stub_downloader: Any, tmp_path: Path) -> None:
    stub_downloader(error=KeyboardInterrupt())
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.INTERRUPTED)


def test_unexpected_errors_are_caught(stub_downloader: Any, tmp_path: Path) -> None:
    stub_downloader(error=RuntimeError("boom"))
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.UNEXPECTED_ERROR)


# -- behaviour ----------------------------------------------------------


def test_info_prints_metadata_without_downloading(
    stub_downloader: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = stub_downloader()
    code = run([VALID_URL, "--info", "--output", str(tmp_path)])
    assert code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "Example Video" in flat(out)
    assert "Example Channel" in flat(out)
    assert stub.requests[0].info_only is True


def test_unknown_hosts_are_attempted_with_a_notice(
    stub_downloader: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    final = tmp_path / "v.mp4"
    stub_downloader(result_path=final)
    code = run(["https://vimeo.com/12345", "--output", str(tmp_path)])
    assert code == int(ExitCode.SUCCESS)
    assert "not one of the explicitly supported" in flat(capsys.readouterr().err)


def test_supported_services_are_named(
    stub_downloader: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_downloader(result_path=tmp_path / "v.mp4")
    run(["https://www.tiktok.com/@user/video/1", "--output", str(tmp_path)])
    assert "TikTok" in flat(capsys.readouterr().err)


def test_the_output_environment_variable_is_used(
    stub_downloader: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_OUTPUT_DIR, str(tmp_path / "fromenv"))
    stub = stub_downloader(result_path=tmp_path / "v.mp4")
    run([VALID_URL])
    assert stub.requests[0].output_dir == (tmp_path / "fromenv").resolve()


def test_audio_conversion_without_ffmpeg_exits_with_the_ffmpeg_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real Downloader is used here: build_ydl_opts must refuse the request."""
    monkeypatch.setattr(
        cli_module, "detect_ffmpeg", lambda _=None: cli_module.FFmpegStatus(None, None)
    )
    code = run([VALID_URL, "--audio", "--audio-format", "mp3", "--output", str(tmp_path)])
    assert code == int(ExitCode.FFMPEG_REQUIRED)
    assert "FFmpeg" in flat(capsys.readouterr().err)


def test_missing_ffmpeg_warns_but_continues_for_video(
    stub_downloader: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module, "detect_ffmpeg", lambda _=None: cli_module.FFmpegStatus(None, None)
    )
    stub_downloader(result_path=tmp_path / "v.mp4")
    code = run([VALID_URL, "--output", str(tmp_path)])
    assert code == int(ExitCode.SUCCESS)
    err = capsys.readouterr().err
    assert "FFmpeg was not found" in flat(err)
    assert "pre-merged formats" in flat(err)
