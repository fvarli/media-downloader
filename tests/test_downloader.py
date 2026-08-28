"""Downloader behaviour, exercised against a fake yt-dlp. No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

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
        # A postprocessor bound to this object reads its options to find
        # FFmpeg, the way yt-dlp's own `params` does. Empty is enough: this
        # test is about which processors get attached, not about FFmpeg.
        params: ClassVar[dict[str, Any]] = {}

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


# -- what the user is told when no format matched ------------------------
#
# A real YouTube download failed with "Requested format is not available" and
# was reported as MEDIA_UNAVAILABLE, telling the owner the video might be
# private, removed, age-restricted, region-locked or DRM-protected. yt-dlp had
# said none of those things.


def test_a_format_failure_is_not_reported_as_inaccessible_media() -> None:
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error
    from media_downloader.errors import MediaUnavailableError, NoFormatMatchError

    error = _classify_download_error(
        DownloadError("ERROR: [youtube] abc: Requested format is not available. Use --list-formats")
    )
    assert isinstance(error, NoFormatMatchError)
    assert not isinstance(error, MediaUnavailableError)


def test_the_format_message_never_claims_a_restriction() -> None:
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error

    error = _classify_download_error(DownloadError("Requested format is not available"))
    said = f"{error.message} {error.hint}".lower()
    for claim in ("private", "drm", "region", "age-restricted", "removed"):
        assert claim not in said, claim


def test_the_format_message_suggests_something_that_might_work() -> None:
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error

    error = _classify_download_error(DownloadError("Requested format is not available"))
    assert "best" in str(error.hint).lower()


def test_the_exit_code_is_unchanged_by_the_new_class() -> None:
    """A more precise error must not quietly change the CLI contract."""
    from media_downloader.errors import DownloadFailedError, NoFormatMatchError

    assert NoFormatMatchError("x").exit_code == DownloadFailedError("x").exit_code


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
        "ERROR: [youtube] abc: Video unavailable",
        "This video is DRM protected",
    ],
)
def test_genuinely_inaccessible_media_is_still_reported_as_such(message: str) -> None:
    """Correcting one misclassification must not lose the true ones."""
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error
    from media_downloader.errors import MediaUnavailableError

    assert isinstance(_classify_download_error(DownloadError(message)), MediaUnavailableError)


# -- telling the user what actually arrived ------------------------------


def _outcome(info: dict[str, Any], quality: str = "best") -> Any:
    from media_downloader.config import build_request
    from media_downloader.downloader import Downloader

    request = build_request(url="https://x.com/a/status/1", output="/tmp/o", quality=quality)
    return Downloader._describe_selection(info, request)


def test_an_ordinary_download_says_nothing_surprising() -> None:
    outcome = _outcome(
        {
            "requested_formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 1080},
                {"vcodec": "none", "acodec": "mp4a"},
            ]
        }
    )
    assert outcome.selection == "video_plus_audio"
    assert outcome.notices() == []


def test_a_silent_file_is_never_handed_over_silently() -> None:
    outcome = _outcome({"requested_formats": [{"vcodec": "vp9", "acodec": "none", "height": 1080}]})
    assert outcome.selection == "fallback_video_only"
    assert any("without audio" in note for note in outcome.notices())


def test_a_muxed_result_is_recognised() -> None:
    outcome = _outcome({"vcodec": "avc1", "acodec": "mp4a", "height": 360})
    assert outcome.selection == "muxed"
    assert outcome.notices() == []


def test_a_cap_that_could_not_be_honoured_is_stated() -> None:
    outcome = _outcome(
        {
            "requested_formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 1080},
                {"vcodec": "none", "acodec": "mp4a"},
            ]
        },
        quality="360",
    )
    assert outcome.cap_exceeded is True
    assert any("360p was not available" in note for note in outcome.notices())


def test_a_cap_that_was_honoured_says_nothing() -> None:
    outcome = _outcome(
        {
            "requested_formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 720},
                {"vcodec": "none", "acodec": "mp4a"},
            ]
        },
        quality="1080",
    )
    assert outcome.cap_exceeded is False
    assert outcome.notices() == []


def test_best_quality_never_reports_an_exceeded_cap() -> None:
    """ "best" is not a number, so there is no cap to exceed."""
    outcome = _outcome(
        {
            "requested_formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 2160},
                {"vcodec": "none", "acodec": "mp4a"},
            ]
        }
    )
    assert outcome.cap_exceeded is False


# -- a conversion that failed after the media arrived ---------------------
#
# Universal downloaded a real video on the owner's Windows machine and then
# failed in postprocessing, because the normaliser could not find the managed
# ffprobe. The message said "The download failed", while a complete, playable
# file sat in the downloads folder -- so the report and the disk disagreed.


def test_a_failed_conversion_is_not_reported_as_a_failed_download() -> None:
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error
    from media_downloader.errors import CompatibilityConversionError

    error = _classify_download_error(
        DownloadError("Postprocessing: ffprobe not found. Please install or provide the path"),
        universal=True,
    )
    assert isinstance(error, CompatibilityConversionError)


def test_the_conversion_message_accounts_for_the_file_on_disk() -> None:
    """The user can see the file. Saying nothing about it is what confused."""
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error

    error = _classify_download_error(
        DownloadError("Postprocessing: ffprobe not found"), universal=True
    )
    said = f"{error.message} {error.hint}".lower()
    assert "downloaded" in said
    assert "kept" in said
    assert "original" in said


def test_a_failed_audio_conversion_is_not_called_a_compatibility_problem() -> None:
    """Audio extraction is a postprocessor too, and Universal is about video.

    Describing a failed MP3 conversion as a playback-compatibility failure
    would be the same shape of confident wrong answer as the one this fixes.
    """
    from yt_dlp.utils import DownloadError

    from media_downloader.downloader import _classify_download_error
    from media_downloader.errors import CompatibilityConversionError

    error = _classify_download_error(
        DownloadError("Postprocessing: audio conversion failed"), universal=False
    )
    assert not isinstance(error, CompatibilityConversionError)


def test_a_conversion_failure_keeps_the_exit_code() -> None:
    from media_downloader.errors import CompatibilityConversionError, DownloadFailedError

    assert CompatibilityConversionError("x").exit_code == DownloadFailedError("x").exit_code


@pytest.mark.parametrize(
    ("exc_name", "code"),
    [
        ("NoFormatMatchError", "FORMAT_UNAVAILABLE"),
        ("CompatibilityConversionError", "COMPATIBILITY_CONVERSION_FAILED"),
    ],
)
def test_the_support_log_can_tell_the_two_apart(exc_name: str, code: str) -> None:
    """Both are download failures; the log line is what support reads.

    Left as plain DOWNLOAD_FAILED, a finished file on disk and an empty
    downloads folder produce the same record.
    """
    import media_downloader.errors as errors_module
    from media_downloader.web.jobs import _error_code

    assert _error_code(getattr(errors_module, exc_name)("x")) == code
