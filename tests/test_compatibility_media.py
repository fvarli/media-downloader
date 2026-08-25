"""Compatibility against real media and real format ordering.

Two things the decision table cannot prove on its own: that yt-dlp's format
sorting actually prefers what we think it does, and that the pipeline turns a
genuinely incompatible file into a playable one.

The format-sorting tests are offline -- they use yt-dlp's own sorter against
synthetic format dictionaries. The media test builds its fixture with FFmpeg at
run time and skips when FFmpeg is absent, so the ordinary suite stays offline
and no binary media is ever committed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from media_downloader.compatibility import (
    MediaProbe,
    ffmpeg_output_arguments,
    plan_for,
    validate_universal,
)
from media_downloader.options import UNIVERSAL_FORMAT_SORT

# -- format selection ----------------------------------------------------
#
# The rule the owner asked for: resolution and frame rate decide first, and a
# compatible codec is only a tie-breaker. Quality is never traded away to avoid
# a transcode -- 4K is not silently downgraded to 1080p.


def _format(fid: str, vcodec: str, height: int, fps: int = 30, ext: str = "mp4") -> dict[str, Any]:
    return {
        "format_id": fid,
        "url": f"https://example.invalid/{fid}",
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "fps": fps,
        "ext": ext,
        "protocol": "https",
    }


def _best_first(formats: list[dict[str, Any]]) -> list[str]:
    from yt_dlp import YoutubeDL
    from yt_dlp.YoutubeDL import FormatSorter

    ydl = YoutubeDL({"quiet": True})
    sorter = FormatSorter(ydl, list(UNIVERSAL_FORMAT_SORT))
    return [f["format_id"] for f in sorted(formats, key=sorter.calculate_preference, reverse=True)]


H264_2160 = _format("h264-2160", "avc1.640034", 2160)
VP9_2160 = _format("vp9-2160", "vp09.00.50.08", 2160, ext="webm")
AV1_2160 = _format("av1-2160", "av01.0.12M.08", 2160)
H264_1080 = _format("h264-1080", "avc1.640028", 1080)
VP9_1080 = _format("vp9-1080", "vp09.00.40.08", 1080, ext="webm")


def test_a_compatible_stream_wins_when_it_costs_no_quality() -> None:
    """Same resolution, so choosing H.264 is a free remux instead of an encode."""
    assert _best_first([VP9_2160, AV1_2160, H264_2160])[0] == "h264-2160"


def test_resolution_is_never_traded_away_to_avoid_a_transcode() -> None:
    """The YouTube case: 2160p exists only as VP9/AV1. Taking the 1080p H.264
    would be a silent quality downgrade, so the 2160p source wins and is
    normalised afterwards instead."""
    ordered = _best_first([VP9_2160, AV1_2160, H264_1080, VP9_1080])
    assert ordered[0] in {"vp9-2160", "av1-2160"}
    assert ordered[0] != "h264-1080"


def test_frame_rate_also_outranks_the_codec_preference() -> None:
    """60fps VP9 beats 30fps H.264 at the same resolution: frame rate is
    quality, and quality decides before convenience."""
    h264_30 = _format("h264-1080-30", "avc1.640028", 1080, fps=30)
    vp9_60 = _format("vp9-1080-60", "vp09.00.40.08", 1080, fps=60, ext="webm")
    assert _best_first([h264_30, vp9_60])[0] == "vp9-1080-60"


def test_the_sort_is_a_preference_and_removes_nothing() -> None:
    """Every format stays selectable; only the ranking changes."""
    formats = [VP9_2160, H264_1080]
    assert set(_best_first(formats)) == {"vp9-2160", "h264-1080"}


# -- real media ----------------------------------------------------------

ffmpeg_required = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs FFmpeg to build and inspect the fixture",
)


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True, timeout=600)


def _probe(path: Path) -> MediaProbe:
    import json

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-print_format",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return MediaProbe.from_ffprobe(json.loads(result.stdout))


def _make_fixture(path: Path, *, vcodec: str, acodec: str | None, pix_fmt: str = "yuv420p") -> None:
    """Build a tiny deterministic clip.

    Note what is *not* here: HE-AAC. Producing it needs libfdk_aac, which is
    nonfree and absent from the FFmpeg this project ships and from CI's. The
    native AAC encoder only produces LC. So the incompatible-audio fixture uses
    Opus, and HE-AAC detection is covered in test_compatibility.py against the
    probe data the validator actually consumes.
    """
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=10:duration=1",
    ]
    if acodec is not None:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    args += ["-c:v", vcodec, "-pix_fmt", pix_fmt, "-b:v", "150k"]
    args += ["-c:a", acodec] if acodec is not None else ["-an"]
    args += ["-shortest", str(path)]
    _run(*args)


def _normalise(source: Path, target: Path) -> None:
    """Run exactly the arguments the application would run."""
    plan = plan_for(_probe(source))
    _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        *ffmpeg_output_arguments(plan),
        str(target),
    )


@ffmpeg_required
def test_the_real_regression_becomes_playable(tmp_path: Path) -> None:
    """VP9 video with non-AAC audio in MP4 -- the shape that reached an iPhone
    and would not play -- must come out as H.264 + AAC-LC + yuv420p in MP4."""
    source = tmp_path / "incompatible.mp4"
    _make_fixture(source, vcodec="libvpx-vp9", acodec="libopus")

    before = _probe(source)
    assert before.video is not None and before.video.codec == "vp9"
    assert validate_universal(before).ok is False

    target = tmp_path / "universal.mp4"
    _normalise(source, target)

    verdict = validate_universal(_probe(target))
    assert verdict.ok is True, verdict.problems
    assert verdict.video_codec == "h264"
    assert verdict.audio_codec == "aac"
    assert verdict.audio_profile == "LC"
    assert verdict.pix_fmt == "yuv420p"
    assert verdict.container == "mp4"
    assert target.stat().st_size > 0


@ffmpeg_required
def test_an_already_compatible_file_keeps_its_video_bit_for_bit(tmp_path: Path) -> None:
    """The optimisation that matters: no generation loss when nothing is wrong.

    Compares the encoded video payload rather than the file, because the
    container is rewritten for faststart even when the streams are copied.
    """
    source = tmp_path / "compatible.mp4"
    _make_fixture(source, vcodec="libx264", acodec="aac")
    assert validate_universal(_probe(source)).ok is True

    plan = plan_for(_probe(source))
    assert not plan.transcodes_anything

    target = tmp_path / "out.mp4"
    _normalise(source, target)
    assert validate_universal(_probe(target)).ok is True

    def video_payload(path: Path) -> bytes:
        raw = path.with_suffix(".h264")
        _run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-c:v",
            "copy",
            "-an",
            "-f",
            "h264",
            str(raw),
        )
        return raw.read_bytes()

    assert video_payload(source) == video_payload(target)


@ffmpeg_required
def test_only_the_audio_is_touched_when_only_the_audio_is_wrong(tmp_path: Path) -> None:
    source = tmp_path / "h264_opus.mp4"
    _make_fixture(source, vcodec="libx264", acodec="libopus")

    plan = plan_for(_probe(source))
    assert plan.action == "transcode_audio"

    target = tmp_path / "out.mp4"
    _normalise(source, target)
    verdict = validate_universal(_probe(target))
    assert verdict.ok is True, verdict.problems
    assert verdict.audio_codec == "aac"


@ffmpeg_required
def test_a_video_without_audio_normalises_to_a_valid_mp4(tmp_path: Path) -> None:
    source = tmp_path / "silent.webm"
    _make_fixture(source, vcodec="libvpx-vp9", acodec=None)

    target = tmp_path / "out.mp4"
    _normalise(source, target)

    probe = _probe(target)
    assert probe.audio is None
    assert validate_universal(probe).ok is True


@ffmpeg_required
@pytest.mark.skipif(
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "encoder=libaom-av1"], capture_output=True, text=True
    ).returncode
    != 0,
    reason="this FFmpeg cannot encode AV1, so the fixture cannot be built",
)
def test_av1_is_normalised_to_h264(tmp_path: Path) -> None:
    source = tmp_path / "av1.mp4"
    _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=5:duration=1",
        "-c:v",
        "libaom-av1",
        "-cpu-used",
        "8",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(source),
    )
    assert _probe(source).video is not None

    target = tmp_path / "out.mp4"
    _normalise(source, target)
    assert validate_universal(_probe(target)).video_codec == "h264"


@ffmpeg_required
def test_a_conversion_that_cannot_work_fails_loudly(tmp_path: Path) -> None:
    """A truncated file must produce an error, never a silent empty result."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not media at all")
    with pytest.raises(subprocess.CalledProcessError):
        _run(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(broken),
            "-c:v",
            "libx264",
            str(tmp_path / "out.mp4"),
        )


@ffmpeg_required
def test_measured_cost_of_a_conversion(tmp_path: Path) -> None:
    """Measured rather than speculated, per the phase requirements.

    Not an assertion about speed -- runners vary far too much for that to mean
    anything. It records the numbers so a regression in the pipeline shows up
    as a reported figure rather than a feeling.
    """
    source = tmp_path / "sample.mp4"
    _make_fixture(source, vcodec="libvpx-vp9", acodec="libopus")
    target = tmp_path / "out.mp4"

    started = time.monotonic()
    _normalise(source, target)
    elapsed = time.monotonic() - started

    probe = _probe(source)
    print(
        f"\n  input  {probe.video.codec if probe.video else '?'} "
        f"{source.stat().st_size:,} bytes"
        f"\n  action {plan_for(probe).action}"
        f"\n  time   {elapsed:.2f}s"
        f"\n  output {target.stat().st_size:,} bytes"
    )
    assert target.stat().st_size > 0
