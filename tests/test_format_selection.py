"""Which format our selector actually gets, against real format matrices.

A real YouTube download failed with "Requested format is not available" after
FFmpeg and Deno were both installed, and was then reported to the user as
though the media were private or region-locked. These tests pin down what the
selector does, using yt-dlp's own selection engine rather than a reimplemented
guess of it -- ``_select_formats`` is the method the real download path calls,
including the ``incomplete_formats`` context that changes how ``b`` behaves.

Entirely offline: the matrices are synthetic, so nothing here depends on what
YouTube happens to serve today.
"""

from __future__ import annotations

from typing import Any

import pytest
from yt_dlp import YoutubeDL

from media_downloader.options import UNIVERSAL_FORMAT_SORT, build_format_selector


def fmt(
    fid: str,
    *,
    v: str | None = None,
    a: str | None = None,
    h: int | None = None,
    ext: str = "mp4",
) -> dict[str, Any]:
    """One format, shaped the way an extractor reports it."""
    return {
        "format_id": fid,
        "url": f"https://example.invalid/{fid}",
        "ext": ext,
        "protocol": "https",
        "vcodec": v or "none",
        "acodec": a or "none",
        "height": h,
    }


def chosen(quality: str, formats: list[dict[str, Any]], *, sort: bool = False) -> list[str]:
    """What the download would actually select, or [] if nothing matched.

    Sorted before selecting, because that is the order the real pipeline uses:
    a format selector picks the *best* candidate, and "best" means last after
    sorting. Selecting from an unsorted list tests nothing about quality.
    """
    params: dict[str, Any] = {"quiet": True}
    if sort:
        params["format_sort"] = list(UNIVERSAL_FORMAT_SORT)
    ydl = YoutubeDL(params)

    info = {"formats": [dict(f) for f in formats]}
    ydl.sort_formats(info)

    selector = ydl.build_format_selector(build_format_selector(quality, ffmpeg_available=True))
    try:
        return [m["format_id"] for m in ydl._select_formats(info["formats"], selector)]
    except Exception:
        return []


def universal(quality: str, formats: list[dict[str, Any]]) -> list[str]:
    return chosen(quality, formats, sort=True)


def original(quality: str, formats: list[dict[str, Any]]) -> list[str]:
    return chosen(quality, formats, sort=False)


# -- the shapes a real site serves ---------------------------------------

H264_1080 = fmt("137", v="avc1.640028", h=1080)
H264_720 = fmt("136", v="avc1.4d401f", h=720)
AAC = fmt("140", a="mp4a.40.2")
VP9_2160 = fmt("313", v="vp09.00.50.08", h=2160, ext="webm")
VP9_1080 = fmt("248", v="vp09.00.40.08", h=1080, ext="webm")
AV1_2160 = fmt("401", v="av01.0.12M.08", h=2160)
OPUS = fmt("251", a="opus", ext="webm")
MUXED_360 = fmt("18", v="avc1.42001E", a="mp4a.40.2", h=360)


def test_a_compatible_source_is_used_when_it_costs_nothing() -> None:
    """H.264 with AAC at the requested quality: no conversion needed."""
    assert universal("1080", [H264_1080, AAC]) == ["137+140"]


def test_universal_takes_the_higher_resolution_source_over_a_compatible_one() -> None:
    """The point of Universal: quality is never traded for codec convenience.

    2160p exists only as VP9, and 1080p H.264 is right there. Taking the 1080p
    would be a silent downgrade; the 2160p is taken and normalised afterwards.
    """
    picked = universal("best", [VP9_2160, H264_1080, OPUS, AAC])
    assert picked and picked[0].startswith("313")


def test_universal_works_when_only_vp9_and_opus_exist() -> None:
    """Nothing compatible to prefer, so it takes what there is and converts."""
    assert universal("best", [VP9_1080, OPUS]) == ["248+251"]


def test_universal_works_when_only_av1_and_opus_exist() -> None:
    assert universal("best", [AV1_2160, OPUS]) == ["401+251"]


def test_original_keeps_the_source_codecs() -> None:
    """Original must not demand MP4 or H.264 -- that is Universal's job."""
    assert original("best", [VP9_2160, OPUS]) == ["313+251"]


def test_original_does_not_prefer_h264_at_a_lower_resolution() -> None:
    picked = original("best", [VP9_2160, H264_1080, OPUS, AAC])
    assert picked and picked[0].startswith("313")


def test_separate_streams_are_combined_when_no_muxed_format_exists() -> None:
    """The ordinary modern case: real YouTube videos measured zero muxed
    formats, so this is not an edge case but the normal path."""
    assert original("best", [VP9_1080, OPUS]) == ["248+251"]


def test_a_muxed_format_is_used_when_that_is_all_there_is() -> None:
    assert original("best", [MUXED_360]) == ["18"]


# -- the quality cap -----------------------------------------------------


def test_the_cap_is_an_upper_bound_not_an_exact_demand() -> None:
    """1080 requested where only 720 exists gives 720, not an error."""
    assert original("1080", [H264_720, AAC]) == ["136+140"]


def test_a_cap_that_cannot_be_met_falls_back_to_the_lowest_available() -> None:
    """The one selector hole this phase actually closes.

    Asking for 360 where the smallest stream is 1080 used to match nothing at
    all and fail the download. Somebody who asks for 360p wants a small file,
    not an error, so the lowest available is taken and they are told the cap
    could not be honoured.
    """
    assert original("360", [H264_1080, AAC]) == ["137+140"]


def test_the_old_chain_really_did_fail_that_case() -> None:
    """Guards the claim above rather than asserting it in prose."""
    ydl = YoutubeDL({"quiet": True})
    info = {"formats": [dict(H264_1080), dict(AAC)]}
    ydl.sort_formats(info)
    old = ydl.build_format_selector("bv*[height<=?360]+ba/b[height<=?360]/b")
    try:
        picked = [m["format_id"] for m in ydl._select_formats(info["formats"], old)]
    except Exception:
        picked = []
    assert picked == []


# -- video with no audio -------------------------------------------------


def test_a_video_with_no_audio_at_all_still_downloads() -> None:
    """A silent file is a poor result; refusing a perfectly good video stream
    is a worse one. The user is told what they got."""
    assert original("best", [VP9_1080]) == ["248"]


def test_a_capped_video_with_no_audio_still_downloads() -> None:
    assert original("1080", [H264_720]) == ["136"]


# -- nothing at all ------------------------------------------------------


def test_no_formats_at_all_selects_nothing() -> None:
    """Which is what produces the error -- and it must be reported as a format
    problem, never as the media being private or region-locked."""
    assert original("best", []) == []


@pytest.mark.parametrize("quality", ["best", "worst", "2160", "1080", "720", "360"])
def test_every_quality_handles_the_ordinary_case(quality: str) -> None:
    assert original(quality, [VP9_2160, VP9_1080, H264_720, OPUS, AAC])


@pytest.mark.parametrize("quality", ["best", "1080", "360"])
def test_the_codec_preference_never_removes_a_candidate(quality: str) -> None:
    """Universal sorts; it does not filter. Whatever Original can reach,
    Universal can reach too -- it may simply order the options differently."""
    formats = [VP9_2160, H264_1080, OPUS, AAC]
    assert bool(universal(quality, formats)) == bool(original(quality, formats))
