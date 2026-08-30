"""Whether the reported selection matches the file that was produced.

A real macOS run reported a completed Universal download while the normaliser
had probed the finished file and found no audio at all. The two facts come from
different places and nothing reconciled them:

* ``_describe_selection`` reads ``acodec`` off the formats the *extractor said
  it selected*;
* ``UniversalCompatibilityPP`` reads the *file that actually exists*, with
  ffprobe.

When they disagreed, the log said ``selection=video_plus_audio`` for a silent
file and -- the part that reaches a person -- ``SelectionOutcome.notices()``
stayed empty, so the "downloaded video without audio" warning never appeared.
Universal presented a silent video as an ordinary success.

Original mode never probes, so it keeps reporting from metadata; that is a
known limitation rather than something these tests pretend to cover.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_downloader.downloader import Downloader
from media_downloader.normalize import FINAL_AUDIO_CODEC_FIELD


def describe(info: dict[str, Any], quality: str = "best") -> Any:
    """Run the real reporting path against one info dict."""
    from media_downloader.config import DownloadRequest

    request = DownloadRequest(url="https://example.invalid/v", output_dir=None, quality=quality)  # type: ignore[arg-type]
    return Downloader._describe_selection(info, request)


def merged(*, acodec: str | None = "mp4a.40.2", height: int = 1080) -> dict[str, Any]:
    """What the extractor reports for an ordinary video+audio merge."""
    return {
        "requested_formats": [
            {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": height},
            {"format_id": "140", "vcodec": "none", "acodec": acodec or "none"},
        ]
    }


# -- the file wins -------------------------------------------------------


def test_a_silent_file_is_not_reported_as_video_plus_audio() -> None:
    """The exact contradiction seen on a real machine."""
    info = merged()
    info[FINAL_AUDIO_CODEC_FIELD] = None  # ffprobe found no audio stream

    outcome = describe(info)

    assert outcome.selection == "fallback_video_only"
    assert outcome.has_audio is False


def test_the_user_is_told_when_the_file_turned_out_silent() -> None:
    """The assertion that matters. A notice the user never sees is the bug."""
    info = merged()
    info[FINAL_AUDIO_CODEC_FIELD] = None

    notices = describe(info).notices()

    assert any("without audio" in note for note in notices), notices


def test_the_old_behaviour_really_did_stay_quiet() -> None:
    """Guards the claim above rather than asserting it in prose.

    Without the probed fact -- which is exactly Original mode, and was every
    mode before this -- the same metadata produces no warning at all.
    """
    outcome = describe(merged())

    assert outcome.selection == "video_plus_audio"
    assert outcome.notices() == []


def test_audio_found_in_the_file_corrects_the_other_direction_too() -> None:
    """Consistency both ways: a warning about silence that is not there would
    be its own kind of wrong."""
    info = merged(acodec=None)  # metadata claims no audio
    info[FINAL_AUDIO_CODEC_FIELD] = "aac"  # the file has some

    outcome = describe(info)

    assert outcome.selection == "video_plus_audio"
    assert outcome.has_audio is True
    assert outcome.notices() == []


def test_a_muxed_format_is_corrected_as_well() -> None:
    info: dict[str, Any] = {"vcodec": "avc1", "acodec": "mp4a.40.2", "height": 360}
    info[FINAL_AUDIO_CODEC_FIELD] = None

    assert describe(info).selection == "fallback_video_only"


# -- where the fact is read from -----------------------------------------


def test_the_fact_is_found_in_the_per_download_entry() -> None:
    """yt-dlp collects the dict a postprocessor mutated into
    ``requested_downloads``, so the answer can arrive nested."""
    info = merged()
    info["requested_downloads"] = [{FINAL_AUDIO_CODEC_FIELD: None}]

    assert describe(info).selection == "fallback_video_only"


# -- not probed is not the same as silent --------------------------------


def test_an_unprobed_download_keeps_reporting_from_metadata() -> None:
    """Original mode never probes. Absent must not be read as "no audio", or
    every Original download would claim to be silent."""
    outcome = describe(merged())

    assert outcome.selection == "video_plus_audio"
    assert outcome.has_audio is True


@pytest.mark.parametrize("quality", ["best", "1080", "360"])
def test_the_quality_cap_notice_is_unaffected(quality: str) -> None:
    """The other fallback keeps working alongside this one."""
    info = merged(height=1080)
    info[FINAL_AUDIO_CODEC_FIELD] = "aac"

    outcome = describe(info, quality)

    assert outcome.cap_exceeded is (quality == "360")
