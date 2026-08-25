"""Downloader behaviour, exercised against a fake yt-dlp. No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from media_downloader.downloader import Downloader, MediaInfo
from media_downloader.errors import (
    DownloadFailedError,
    MediaUnavailableError,
    OutputError,
)
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.naming import AUTO_NAME_FIELD

from .conftest import FakeYoutubeDL

SAMPLE_INFO: dict[str, Any] = {
    "title": "Example Video",
    "uploader": "Example Channel",
    "duration": 213,
    "extractor_key": "Youtube",
    "webpage_url": "https://www.youtube.com/watch?v=abc",
    "width": 1920,
    "height": 1080,
    "filesize_approx": 12_345_678,
    "ext": "mp4",
}


def make_downloader(
    ffmpeg: FFmpegStatus, **fake_kwargs: Any
) -> tuple[Downloader, list[FakeYoutubeDL]]:
    created: list[FakeYoutubeDL] = []

    def factory(opts: dict[str, Any]) -> FakeYoutubeDL:
        fake = FakeYoutubeDL(opts, **fake_kwargs)
        created.append(fake)
        return fake

    return Downloader(ffmpeg, ydl_factory=factory), created


def test_fetch_info_maps_the_metadata(ffmpeg_present: FFmpegStatus, request_factory: Any) -> None:
    downloader, created = make_downloader(ffmpeg_present, info=SAMPLE_INFO)
    info = downloader.fetch_info(request_factory(info_only=True))

    assert isinstance(info, MediaInfo)
    assert info.title == "Example Video"
    assert info.uploader == "Example Channel"
    assert info.duration_seconds == 213
    assert info.extractor == "Youtube"
    assert created[0].calls == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False)]


def test_fetch_info_never_downloads(ffmpeg_present: FFmpegStatus, request_factory: Any) -> None:
    downloader, created = make_downloader(ffmpeg_present, info=SAMPLE_INFO)
    downloader.fetch_info(request_factory(info_only=True))
    assert created[0].opts["skip_download"] is True
    assert all(download is False for _, download in created[0].calls)


def test_download_creates_the_output_directory(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    target = tmp_path / "fresh" / "nested"
    final = target / "Example Video [abc].mp4"
    downloader, _ = make_downloader(ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(final))
    downloader.download(request_factory(output_dir=target))
    assert target.is_dir()


def test_download_returns_the_post_hook_path(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    """post_hooks fire after postprocessing, so they win over every fallback."""
    final = tmp_path / "downloads" / "Example Video [abc].mp3"
    info = {**SAMPLE_INFO, "requested_downloads": [{"filepath": str(tmp_path / "stale.webm")}]}
    downloader, _ = make_downloader(ffmpeg_present, info=info, post_hook_path=str(final))

    result = downloader.download(request_factory(output_dir=tmp_path / "downloads"))
    assert result.path == final.resolve()


def test_download_falls_back_to_requested_downloads(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    expected = tmp_path / "downloads" / "Example Video [abc].mkv"
    info = {**SAMPLE_INFO, "requested_downloads": [{"filepath": str(expected)}]}
    downloader, _ = make_downloader(ffmpeg_present, info=info)

    result = downloader.download(request_factory(output_dir=tmp_path / "downloads"))
    assert result.path == expected.resolve()


def test_download_errors_when_no_path_can_be_determined(
    ffmpeg_present: FFmpegStatus, request_factory: Any
) -> None:
    downloader, _ = make_downloader(ffmpeg_present, info=dict(SAMPLE_INFO))
    with pytest.raises(OutputError):
        downloader.download(request_factory())


def test_playlist_results_use_the_first_entry(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    expected = tmp_path / "first.mp4"
    playlist = {
        "entries": [None, {**SAMPLE_INFO, "requested_downloads": [{"filepath": str(expected)}]}]
    }
    downloader, _ = make_downloader(ffmpeg_present, info=playlist)
    assert downloader.download(request_factory()).path == expected.resolve()


def test_empty_result_is_reported(ffmpeg_present: FFmpegStatus, request_factory: Any) -> None:
    downloader, _ = make_downloader(ffmpeg_present, info=None)
    with pytest.raises(DownloadFailedError):
        downloader.fetch_info(request_factory())


def test_the_session_is_always_closed(ffmpeg_present: FFmpegStatus, request_factory: Any) -> None:
    downloader, created = make_downloader(
        ffmpeg_present, error=DownloadError("network unreachable")
    )
    with pytest.raises(DownloadFailedError):
        downloader.fetch_info(request_factory())
    assert created[0].closed is True


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: Private video. Sign in if you've been granted access",
        "Video unavailable. This video has been removed by the uploader",
        "This video is age-restricted",
        "The uploader has not made this video available in your country",
        "This video is DRM protected",
        "Sign in to confirm you're not a bot",
    ],
)
def test_access_restrictions_map_to_a_distinct_exit_code(
    message: str, ffmpeg_present: FFmpegStatus, request_factory: Any
) -> None:
    downloader, _ = make_downloader(ffmpeg_present, error=DownloadError(message))
    with pytest.raises(MediaUnavailableError) as excinfo:
        downloader.fetch_info(request_factory())
    assert int(excinfo.value.exit_code) == 6


@pytest.mark.parametrize(
    "message",
    ["Unable to download webpage: timed out", "HTTP Error 500: Internal Server Error"],
)
def test_transient_failures_map_to_the_download_error_code(
    message: str, ffmpeg_present: FFmpegStatus, request_factory: Any
) -> None:
    downloader, _ = make_downloader(ffmpeg_present, error=DownloadError(message))
    with pytest.raises(DownloadFailedError) as excinfo:
        downloader.fetch_info(request_factory())
    assert int(excinfo.value.exit_code) == 5
    assert excinfo.value.hint is not None
    assert "pip install -U yt-dlp" in excinfo.value.hint


def test_extractor_errors_are_translated(
    ffmpeg_present: FFmpegStatus, request_factory: Any
) -> None:
    downloader, _ = make_downloader(ffmpeg_present, error=ExtractorError("bad response"))
    with pytest.raises(DownloadFailedError):
        downloader.fetch_info(request_factory())


def test_unsupported_urls_are_explained(ffmpeg_present: FFmpegStatus, request_factory: Any) -> None:
    downloader, _ = make_downloader(
        ffmpeg_present, error=UnsupportedError("https://example.com/nope")
    )
    with pytest.raises(DownloadFailedError) as excinfo:
        downloader.fetch_info(request_factory())
    assert "no extractor" in excinfo.value.message


def test_filesystem_errors_map_to_the_output_code(
    ffmpeg_present: FFmpegStatus, request_factory: Any
) -> None:
    downloader, _ = make_downloader(ffmpeg_present, error=OSError("disk full"))
    with pytest.raises(OutputError) as excinfo:
        downloader.fetch_info(request_factory())
    assert int(excinfo.value.exit_code) == 7


def test_progress_hook_is_registered_when_supplied(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    created: list[FakeYoutubeDL] = []
    final = tmp_path / "v.mp4"

    def factory(opts: dict[str, Any]) -> FakeYoutubeDL:
        fake = FakeYoutubeDL(opts, info=SAMPLE_INFO, post_hook_path=str(final))
        created.append(fake)
        return fake

    downloader = Downloader(ffmpeg_present, ydl_factory=factory, progress_hook=seen.append)
    downloader.download(request_factory())
    assert created[0].opts["progress_hooks"] == [seen.append]


class RecordingYoutubeDL(FakeYoutubeDL):
    """Fake that records postprocessors registered against it."""

    def __init__(self, opts: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(opts, **kwargs)
        self.postprocessors: list[tuple[Any, str]] = []

    def add_post_processor(self, pp: Any, when: str = "post_process") -> None:
        self.postprocessors.append((pp, when))


def make_recording_downloader(
    ffmpeg: FFmpegStatus, **fake_kwargs: Any
) -> tuple[Downloader, list[RecordingYoutubeDL]]:
    created: list[RecordingYoutubeDL] = []

    def factory(opts: dict[str, Any]) -> RecordingYoutubeDL:
        fake = RecordingYoutubeDL(opts, **fake_kwargs)
        created.append(fake)
        return fake

    return Downloader(ffmpeg, ydl_factory=factory), created


def test_automatic_naming_is_registered_before_the_filename_is_built(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    """yt-dlp runs pre_process hooks immediately before prepare_filename."""
    downloader, created = make_recording_downloader(
        ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(tmp_path / "v.mp4")
    )
    downloader.download(request_factory(filename_template=None))

    assert len(created[0].postprocessors) == 1
    _, when = created[0].postprocessors[0]
    assert when == "pre_process"


def test_the_registered_postprocessor_injects_the_clean_stem(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    downloader, created = make_recording_downloader(
        ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(tmp_path / "v.mp4")
    )
    downloader.download(request_factory(filename_template=None))

    pp, _ = created[0].postprocessors[0]
    info = {"title": "Trend - https://t.co/YF86pOpbhn", "id": "2090546322570924033"}
    _, result = pp.run(info)
    assert result[AUTO_NAME_FIELD] == "Trend - 2090546322570924033"


def test_a_custom_filename_skips_automatic_naming_entirely(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    """The user's template must reach yt-dlp without the cleaner attached."""
    downloader, created = make_recording_downloader(
        ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(tmp_path / "v.mp4")
    )
    downloader.download(request_factory(filename_template="%(title)s.%(ext)s"))

    assert created[0].postprocessors == []
    assert created[0].opts["outtmpl"]["default"] == "%(title)s.%(ext)s"


def test_naming_failure_never_aborts_a_download(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    """A broken info dict must degrade to yt-dlp's naming, not raise."""
    downloader, created = make_recording_downloader(
        ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(tmp_path / "v.mp4")
    )
    downloader.download(request_factory(filename_template=None))
    pp, _ = created[0].postprocessors[0]

    class Hostile(dict[str, Any]):
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("metadata exploded")

    _, result = pp.run(Hostile())
    assert AUTO_NAME_FIELD not in result


def test_a_ydl_without_postprocessor_support_still_downloads(
    ffmpeg_present: FFmpegStatus, request_factory: Any, tmp_path: Path
) -> None:
    """The plain fake has no add_post_processor; that must not be fatal."""
    final = tmp_path / "v.mp4"
    downloader, _ = make_downloader(ffmpeg_present, info=SAMPLE_INFO, post_hook_path=str(final))
    assert downloader.download(request_factory(filename_template=None)).path == final.resolve()


# -- playback compatibility ----------------------------------------------


def _registered(request: Any) -> list[str]:
    """Which postprocessors a download would attach, without downloading."""
    attached: list[str] = []

    class Recording:
        def add_post_processor(self, pp: Any, when: str = "post_process") -> None:
            attached.append(f"{type(pp).__name__}@{when}")

        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            return {
                "id": "x",
                "title": "T",
                "ext": "mp4",
                "filepath": "/tmp/x.mp4",
                "extractor_key": "Test",
                "webpage_url": "https://x.com/a/status/1",
            }

        def close(self) -> None:
            return None

    downloader = Downloader(
        FFmpegStatus(Path("ffmpeg"), Path("ffprobe")), ydl_factory=lambda opts: Recording()
    )
    downloader.download(request)
    return attached


def _req(tmp_path: Path, **kwargs: Any) -> Any:
    from media_downloader.config import build_request

    return build_request(url="https://x.com/a/status/1", output=str(tmp_path), **kwargs)


def test_universal_attaches_the_normaliser_after_the_download(tmp_path: Path) -> None:
    from media_downloader.config import CompatibilityMode

    attached = _registered(_req(tmp_path, compatibility=CompatibilityMode.UNIVERSAL))
    assert any("UniversalCompatibilityPP@post_process" in entry for entry in attached), attached


def test_original_attaches_no_normaliser(tmp_path: Path) -> None:
    from media_downloader.config import CompatibilityMode

    attached = _registered(_req(tmp_path, compatibility=CompatibilityMode.ORIGINAL))
    assert not any("UniversalCompatibility" in entry for entry in attached)


def test_audio_downloads_never_get_the_video_normaliser(tmp_path: Path) -> None:
    from media_downloader.config import CompatibilityMode

    attached = _registered(
        _req(tmp_path, audio_only=True, compatibility=CompatibilityMode.UNIVERSAL)
    )
    assert not any("UniversalCompatibility" in entry for entry in attached)


def test_automatic_naming_still_applies_alongside_normalisation(tmp_path: Path) -> None:
    """The v0.1.1 clean-filename behaviour must survive the new phase."""
    from media_downloader.config import CompatibilityMode

    attached = _registered(_req(tmp_path, compatibility=CompatibilityMode.UNIVERSAL))
    assert any("AutoNamePP@pre_process" in entry for entry in attached), attached


def test_a_user_supplied_filename_still_suppresses_automatic_naming(tmp_path: Path) -> None:
    from media_downloader.config import CompatibilityMode

    attached = _registered(
        _req(tmp_path, filename="mine.%(ext)s", compatibility=CompatibilityMode.UNIVERSAL)
    )
    assert not any("AutoName" in entry for entry in attached)
    assert any("UniversalCompatibility" in entry for entry in attached)
