"""Format selection and the FFmpeg fallback rules (pure logic)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader.errors import FFmpegRequiredError
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.naming import AUTO_OUTPUT_TEMPLATE
from media_downloader.options import (
    build_format_selector,
    build_info_opts,
    build_ydl_opts,
)


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("best", "bv*+ba/b"),
        ("worst", "wv*+wa/w"),
        ("2160", "bv*[height<=?2160]+ba/b[height<=?2160]/b"),
        ("1080", "bv*[height<=?1080]+ba/b[height<=?1080]/b"),
        ("360", "bv*[height<=?360]+ba/b[height<=?360]/b"),
    ],
)
def test_selector_merges_streams_when_ffmpeg_is_available(quality: str, expected: str) -> None:
    assert build_format_selector(quality, ffmpeg_available=True) == expected


@pytest.mark.parametrize("quality", ["best", "worst", "2160", "1080", "360"])
def test_selector_avoids_merging_without_ffmpeg(quality: str) -> None:
    """Without FFmpeg only pre-merged (progressive) formats may be selected."""
    selector = build_format_selector(quality, ffmpeg_available=False)
    assert "+" not in selector
    assert "bv*" not in selector
    assert "wv*" not in selector


def test_selector_still_respects_the_height_cap_without_ffmpeg() -> None:
    assert build_format_selector("720", ffmpeg_available=False) == "b[height<=?720]/b"


def test_video_options_use_the_output_directory(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    request = request_factory(quality="1080")
    opts = build_ydl_opts(request, ffmpeg_present)
    assert opts["format"] == "bv*[height<=?1080]+ba/b[height<=?1080]/b"
    assert opts["paths"]["home"] == str(request.output_dir)
    # Relative template + paths.home, so yt-dlp does not ignore "paths".
    assert opts["outtmpl"]["default"] == AUTO_OUTPUT_TEMPLATE
    assert opts["ffmpeg_location"] == str(ffmpeg_present.location)


def test_filenames_are_windows_safe_on_every_platform(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    """Sanitisation is delegated to yt-dlp and forced to the strictest ruleset."""
    opts = build_ydl_opts(request_factory(), ffmpeg_present)
    assert opts["windowsfilenames"] is True
    assert opts["trim_file_name"] == 200


def test_playlists_are_not_expanded(request_factory: Any, ffmpeg_present: FFmpegStatus) -> None:
    assert build_ydl_opts(request_factory(), ffmpeg_present)["noplaylist"] is True


def test_ffmpeg_location_is_omitted_when_unavailable(
    request_factory: Any, ffmpeg_absent: FFmpegStatus
) -> None:
    assert "ffmpeg_location" not in build_ydl_opts(request_factory(), ffmpeg_absent)


def test_audio_best_adds_an_extraction_postprocessor(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    request = request_factory(audio_only=True, audio_format="best")
    opts = build_ydl_opts(request, ffmpeg_present)
    assert opts["format"] == "ba/b"
    assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert opts["postprocessors"][0]["preferredcodec"] == "best"


def test_audio_best_works_without_ffmpeg_by_keeping_the_source_stream(
    request_factory: Any, ffmpeg_absent: FFmpegStatus
) -> None:
    request = request_factory(audio_only=True, audio_format="best")
    opts = build_ydl_opts(request, ffmpeg_absent)
    assert opts["format"] == "ba/b"
    assert "postprocessors" not in opts


def test_audio_conversion_without_ffmpeg_fails_clearly(
    request_factory: Any, ffmpeg_absent: FFmpegStatus
) -> None:
    request = request_factory(audio_only=True, audio_format="mp3")
    with pytest.raises(FFmpegRequiredError) as excinfo:
        build_ydl_opts(request, ffmpeg_absent)

    error = excinfo.value
    assert int(error.exit_code) == 4
    assert "mp3" in error.message
    assert error.hint is not None
    assert "ffmpeg.org" in error.hint.lower()


def test_hooks_are_only_set_when_provided(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    bare = build_ydl_opts(request_factory(), ffmpeg_present)
    assert "progress_hooks" not in bare
    assert "post_hooks" not in bare

    hooked = build_ydl_opts(
        request_factory(), ffmpeg_present, progress_hooks=[print], post_hooks=[print]
    )
    assert hooked["progress_hooks"] == [print]
    assert hooked["post_hooks"] == [print]


def test_overwrite_flag_is_forwarded(request_factory: Any, ffmpeg_present: FFmpegStatus) -> None:
    assert build_ydl_opts(request_factory(overwrite=True), ffmpeg_present)["overwrites"] is True
    assert build_ydl_opts(request_factory(), ffmpeg_present)["overwrites"] is False


def test_info_options_skip_the_download(request_factory: Any) -> None:
    opts = build_info_opts(request_factory())
    assert opts["skip_download"] is True
    assert opts["noplaylist"] is True


def test_build_ydl_opts_is_pure(request_factory: Any, ffmpeg_present: FFmpegStatus) -> None:
    """Same inputs, same output, and no mutation of the request."""
    request = request_factory(quality="720")
    first = build_ydl_opts(request, ffmpeg_present)
    second = build_ydl_opts(request, ffmpeg_present)
    assert first == second
    assert request.quality == "720"


def test_detected_node_is_enabled_for_yt_dlp(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    """yt-dlp enables Deno only, so a found Node must be switched on."""
    opts = build_ydl_opts(
        request_factory(),
        ffmpeg_present,
        js_runtime=JSRuntimeStatus(name="node", path="/usr/bin/node"),
    )
    assert "node" in opts["js_runtimes"]


def test_deno_leaves_yt_dlps_default_untouched(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    opts = build_ydl_opts(
        request_factory(),
        ffmpeg_present,
        js_runtime=JSRuntimeStatus(name="deno", path="/usr/bin/deno"),
    )
    assert "js_runtimes" not in opts


def test_no_runtime_leaves_yt_dlps_default_untouched(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    opts = build_ydl_opts(request_factory(), ffmpeg_present, js_runtime=JSRuntimeStatus(name=None))
    assert "js_runtimes" not in opts


def test_info_options_also_enable_a_detected_runtime(request_factory: Any) -> None:
    opts = build_info_opts(
        request_factory(), js_runtime=JSRuntimeStatus(name="bun", path="/usr/bin/bun")
    )
    assert "bun" in opts["js_runtimes"]


def test_output_template_stays_relative_so_paths_is_honoured(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    """An absolute outtmpl makes yt-dlp ignore "paths" and misplace .part files."""
    opts = build_ydl_opts(request_factory(filename_template="x.%(ext)s"), ffmpeg_present)
    template = opts["outtmpl"]["default"]
    assert not Path(template).is_absolute()
    assert "/" not in template and "\\" not in template


def test_automatic_template_is_used_when_no_filename_was_given(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    opts = build_ydl_opts(request_factory(filename_template=None), ffmpeg_present)
    assert opts["outtmpl"]["default"] == AUTO_OUTPUT_TEMPLATE


def test_a_custom_template_is_passed_through_untouched(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    """The user owns their --filename; the cleaner must not rewrite it."""
    custom = "%(title)s.%(ext)s"
    opts = build_ydl_opts(request_factory(filename_template=custom), ffmpeg_present)
    assert opts["outtmpl"]["default"] == custom
    assert AUTO_OUTPUT_TEMPLATE not in opts["outtmpl"]["default"]


def test_windows_sanitisation_still_applies_under_automatic_naming(
    request_factory: Any, ffmpeg_present: FFmpegStatus
) -> None:
    """Cleaning is additive: yt-dlp's own safety net stays on."""
    opts = build_ydl_opts(request_factory(filename_template=None), ffmpeg_present)
    assert opts["windowsfilenames"] is True
    assert opts["trim_file_name"] == 200
