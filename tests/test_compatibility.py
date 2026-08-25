"""The playback-compatibility decision table.

A real download produced an MP4 holding VP9 video and HE-AAC audio. It played
on Linux; an iPhone would not play it normally, because ".mp4" names the
container and says nothing about the codecs inside. These tests are the
decision table that prevents that file from ever being reported as finished
again, and they run against ffprobe's own output shape, so they need no media.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_downloader.compatibility import (
    AUDIO_ENCODER,
    VIDEO_ENCODER,
    CompatibilityPlan,
    MediaProbe,
    StreamAction,
    ffmpeg_output_arguments,
    plan_for,
    validate_universal,
)

MP4_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"


def probe_of(
    *,
    video: str | None = "h264",
    video_profile: str | None = "High",
    pix_fmt: str | None = "yuv420p",
    audio: str | None = "aac",
    audio_profile: str | None = "LC",
    container: str = MP4_FORMAT,
) -> MediaProbe:
    """Build a probe exactly as ffprobe -print_format json reports one."""
    streams: list[dict[str, Any]] = []
    if video is not None:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": video,
                "profile": video_profile,
                "pix_fmt": pix_fmt,
            }
        )
    if audio is not None:
        entry: dict[str, Any] = {"codec_type": "audio", "codec_name": audio}
        if audio_profile is not None:
            entry["profile"] = audio_profile
        streams.append(entry)
    return MediaProbe.from_ffprobe({"streams": streams, "format": {"format_name": container}})


# -- the real failure ----------------------------------------------------


def test_the_file_that_would_not_play_on_an_iphone_is_rejected() -> None:
    """VP9 + HE-AAC in MP4: the exact shape ffprobe reported for it."""
    probe = probe_of(video="vp9", video_profile="Profile 0", audio="aac", audio_profile="HE-AAC")

    verdict = validate_universal(probe)
    assert verdict.ok is False
    assert any("vp9" in problem for problem in verdict.problems)
    assert any("HE-AAC" in problem for problem in verdict.problems)


def test_the_file_that_would_not_play_is_fully_normalised() -> None:
    plan = plan_for(
        probe_of(video="vp9", video_profile="Profile 0", audio="aac", audio_profile="HE-AAC")
    )
    assert plan.video is StreamAction.TRANSCODE
    assert plan.audio is StreamAction.TRANSCODE
    assert plan.action == "transcode_video_and_audio"
    assert "vp9" in plan.reason and "HE-AAC" in plan.reason


# -- CASE A: already compatible, do not re-encode ------------------------


def test_an_already_compatible_file_is_never_re_encoded() -> None:
    """Re-encoding a file that already plays costs time and a generation of
    quality for nothing."""
    plan = plan_for(probe_of())

    assert plan.video is StreamAction.COPY
    assert plan.audio is StreamAction.COPY
    assert plan.action == "stream_copy"
    assert plan.reason == "already_universal"

    args = ffmpeg_output_arguments(plan)
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "copy"
    assert VIDEO_ENCODER not in args


def test_a_compatible_file_is_still_remuxed_for_faststart() -> None:
    """Copy is not the same as leaving it alone: faststart is what lets a
    player start before the whole file has arrived, and it cannot be detected
    reliably, so the cheap lossless remux is always done."""
    args = ffmpeg_output_arguments(plan_for(probe_of()))
    assert "+faststart" in args
    assert args[args.index("-f") + 1] == "mp4"


# -- CASE B: video fine, audio not ---------------------------------------


@pytest.mark.parametrize(
    ("codec", "profile"),
    [("aac", "HE-AAC"), ("aac", "HE-AACv2"), ("opus", None), ("vorbis", None), ("mp3", None)],
)
def test_incompatible_audio_alone_never_re_encodes_the_video(
    codec: str, profile: str | None
) -> None:
    plan = plan_for(probe_of(audio=codec, audio_profile=profile))

    assert plan.video is StreamAction.COPY
    assert plan.audio is StreamAction.TRANSCODE
    assert plan.action == "transcode_audio"

    args = ffmpeg_output_arguments(plan)
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == AUDIO_ENCODER
    assert VIDEO_ENCODER not in args


# -- CASE C/D: incompatible video ----------------------------------------


@pytest.mark.parametrize("codec", ["vp9", "av01", "vp8", "theora", "hevc", "mpeg4"])
def test_video_outside_the_target_is_converted_to_h264(codec: str) -> None:
    plan = plan_for(probe_of(video=codec, video_profile=None))

    assert plan.video is StreamAction.TRANSCODE
    args = ffmpeg_output_arguments(plan)
    assert args[args.index("-c:v") + 1] == VIDEO_ENCODER
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


def test_h264_in_a_pixel_format_players_reject_is_converted() -> None:
    """10-bit H.264 is still h264 to ffprobe and still will not play natively."""
    plan = plan_for(probe_of(pix_fmt="yuv420p10le"))
    assert plan.video is StreamAction.TRANSCODE


def test_compatible_video_in_a_non_mp4_container_is_only_remuxed() -> None:
    """WebM holding H.264 and AAC needs a new container, not a new encode."""
    plan = plan_for(probe_of(container="matroska,webm"))
    assert plan.video is StreamAction.COPY
    assert plan.audio is StreamAction.COPY
    assert plan.container_ok is False


# -- CASE E: no audio ----------------------------------------------------


def test_a_video_without_audio_stays_valid() -> None:
    plan = plan_for(probe_of(audio=None))
    assert plan.audio is StreamAction.ABSENT
    assert "-an" in ffmpeg_output_arguments(plan)


def test_a_silent_h264_file_needs_no_encoding() -> None:
    plan = plan_for(probe_of(audio=None))
    assert plan.video is StreamAction.COPY
    assert plan.action == "stream_copy"


def test_a_silent_file_passes_validation() -> None:
    assert validate_universal(probe_of(audio=None)).ok is True


# -- absent information is not failing information -----------------------


def test_audio_with_no_reported_profile_is_judged_on_its_codec() -> None:
    """ffprobe omits the profile key entirely for Opus -- confirmed against a
    real file -- so absent must not be read as wrong."""
    assert validate_universal(probe_of(audio_profile=None)).ok is True
    assert plan_for(probe_of(audio_profile=None)).audio is StreamAction.COPY


def test_video_with_no_reported_pixel_format_is_not_re_encoded_on_a_guess() -> None:
    assert plan_for(probe_of(pix_fmt=None)).video is StreamAction.COPY


def test_cover_art_is_not_mistaken_for_a_video_stream() -> None:
    """An attached picture is a video stream to ffprobe and not to a player."""
    probe = MediaProbe.from_ffprobe(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "disposition": {"attached_pic": 1},
                },
                {"codec_type": "audio", "codec_name": "aac", "profile": "LC"},
            ],
            "format": {"format_name": MP4_FORMAT},
        }
    )
    assert probe.video is None


# -- the verdict ---------------------------------------------------------


def test_a_normalised_file_passes_and_reports_what_it_is() -> None:
    verdict = validate_universal(probe_of())
    assert verdict.ok is True
    assert verdict.problems == ()
    fields = verdict.as_log_fields()
    assert "final_video_codec=h264" in fields
    assert "final_audio_codec=aac" in fields
    assert "final_audio_profile=LC" in fields
    assert "final_container=mp4" in fields


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"video": "vp9"}, "vp9"),
        ({"audio": "opus", "audio_profile": None}, "opus"),
        ({"audio_profile": "HE-AAC"}, "HE-AAC"),
        ({"pix_fmt": "yuv444p"}, "yuv444p"),
        ({"container": "matroska,webm"}, "mp4"),
        ({"video": None}, "no video stream"),
    ],
)
def test_every_way_a_file_can_miss_the_target_is_named(
    kwargs: dict[str, Any], expected: str
) -> None:
    """A verdict has to say what is wrong, because it becomes the error the
    user sees and the record support reads."""
    verdict = validate_universal(probe_of(**kwargs))
    assert verdict.ok is False
    assert any(expected in problem for problem in verdict.problems)


def test_the_log_record_carries_no_source_material() -> None:
    """Diagnostics record the decision, never the media or where it came from."""
    plan = plan_for(probe_of(video="vp9", audio="aac", audio_profile="HE-AAC"))
    text = f"{plan.action} {plan.reason}"
    for forbidden in ("http", "://", "token", "cookie"):
        assert forbidden not in text.lower()


def test_a_plan_reports_whether_any_encoding_is_needed() -> None:
    assert plan_for(probe_of()).transcodes_anything is False
    assert plan_for(probe_of(video="vp9")).transcodes_anything is True
    assert isinstance(plan_for(probe_of()), CompatibilityPlan)
