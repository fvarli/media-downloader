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
    H264_ENCODERS,
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
    assert not any(name in args for name in H264_ENCODERS)


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
    assert not any(name in args for name in H264_ENCODERS)


# -- CASE C/D: incompatible video ----------------------------------------


@pytest.mark.parametrize("codec", ["vp9", "av01", "vp8", "theora", "hevc", "mpeg4"])
def test_video_outside_the_target_is_converted_to_h264(codec: str) -> None:
    plan = plan_for(probe_of(video=codec, video_profile=None))

    assert plan.video is StreamAction.TRANSCODE
    args = ffmpeg_output_arguments(plan)
    assert args[args.index("-c:v") + 1] in H264_ENCODERS
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


# -- which H.264 encoder exists depends on the licence -------------------
#
# libx264 is GPL, so the LGPL FFmpeg this application installs for its own
# users cannot contain it -- that build ships Cisco's libopenh264 instead.
# Hard-coding libx264 failed in CI for exactly those users, which is how this
# was found.

SYSTEM_ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC
 V....D libx265              libx265 H.265 / HEVC
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3
"""

LGPL_ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libopenh264          OpenH264 H.264 / AVC
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 A....D aac                  AAC (Advanced Audio Coding)
"""


def test_the_gpl_build_offers_the_better_encoder() -> None:
    from media_downloader.compatibility import choose_video_encoder, parse_available_encoders

    assert choose_video_encoder(parse_available_encoders(SYSTEM_ENCODERS)) == "libx264"


def test_the_lgpl_build_falls_back_to_openh264() -> None:
    """The build users actually get through the managed install."""
    from media_downloader.compatibility import choose_video_encoder, parse_available_encoders

    available = parse_available_encoders(LGPL_ENCODERS)
    assert "libx264" not in available
    assert choose_video_encoder(available) == "libopenh264"


def test_hardware_encoders_are_never_chosen() -> None:
    """They are neither portable nor deterministic, and h264_nvenc sitting in
    the listing must not be mistaken for something we can rely on."""
    from media_downloader.compatibility import choose_video_encoder, parse_available_encoders

    assert choose_video_encoder(parse_available_encoders(LGPL_ENCODERS)) != "h264_nvenc"


def test_an_ffmpeg_with_no_h264_encoder_is_recognised() -> None:
    from media_downloader.compatibility import choose_video_encoder, parse_available_encoders

    listing = " V....D libvpx-vp9           libvpx VP9\n A....D aac                  AAC\n"
    assert choose_video_encoder(parse_available_encoders(listing)) is None


def test_the_listing_header_is_not_read_as_an_encoder() -> None:
    from media_downloader.compatibility import parse_available_encoders

    found = parse_available_encoders(SYSTEM_ENCODERS)
    assert "=" not in found
    assert "Encoders:" not in found
    assert found == {"libx264", "libx265", "aac", "libmp3lame"}


def test_a_quality_target_is_used_where_the_encoder_supports_one() -> None:
    args = ffmpeg_output_arguments(plan_for(probe_of(video="vp9")), video_encoder="libx264")
    assert "-crf" in args and "-preset" in args
    assert "-b:v" not in args


def test_a_bitrate_is_used_where_the_encoder_supports_no_quality_target() -> None:
    """libopenh264 understands neither -crf nor -preset; passing them would be
    silently ignored and the result unaimed."""
    args = ffmpeg_output_arguments(
        plan_for(probe_of(video="vp9")), video_encoder="libopenh264", source_bitrate=2_000_000
    )
    assert "-crf" not in args and "-preset" not in args
    assert args[args.index("-b:v") + 1] == "4000000"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (None, 6_000_000),
        (0, 6_000_000),
        (100_000, 1_000_000),
        (2_000_000, 4_000_000),
        (50_000_000, 24_000_000),
    ],
)
def test_the_bitrate_is_scaled_and_bounded(source: int | None, expected: int) -> None:
    """H.264 needs more bits than VP9 or AV1 for the same picture, so copying
    the source rate would lose quality visibly -- but not without a ceiling."""
    from media_downloader.compatibility import target_video_bitrate

    assert target_video_bitrate(source) == expected


def test_the_source_bitrate_is_read_from_the_probe() -> None:
    probe = MediaProbe.from_ffprobe(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "vp9", "bit_rate": "3000000"},
            ],
            "format": {"format_name": MP4_FORMAT, "bit_rate": "9999"},
        }
    )
    assert probe.video_bitrate == 3_000_000


def test_the_container_bitrate_is_used_when_the_stream_has_none() -> None:
    probe = MediaProbe.from_ffprobe(
        {
            "streams": [{"codec_type": "video", "codec_name": "vp9"}],
            "format": {"format_name": MP4_FORMAT, "bit_rate": "1500000"},
        }
    )
    assert probe.video_bitrate == 1_500_000
