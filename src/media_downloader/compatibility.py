"""Deciding whether a downloaded file will actually play, and what to do if not.

``.mp4`` is a container, not a promise. A real download from this application
produced an MP4 holding VP9 video and HE-AAC audio: it played on Linux, and iOS
Files would not play it normally, because Apple's native players decode
H.264 and AAC-LC. Nothing in the pipeline had any notion of playback
compatibility -- it asked for "best" and accepted whatever codecs came back.

This module is the missing notion. It is deliberately pure: it takes ffprobe's
own structured output, decides what has to change, and judges the result.
Nothing here runs a process or touches the network, which is what makes the
whole decision table testable offline against probe dictionaries.

The universal target is H.264 + AAC-LC + yuv420p in MP4 with faststart. That
covers iPhone, iPad, macOS, Windows, Android and mainstream browsers. It is
broad native compatibility, not a guarantee about every device ever built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: What "universal" means, concretely.
UNIVERSAL_VIDEO_CODEC = "h264"
UNIVERSAL_AUDIO_CODEC = "aac"
UNIVERSAL_PIX_FMT = "yuv420p"
UNIVERSAL_CONTAINER = "mp4"

#: ffprobe reports an MP4 as this set of mutually compatible brands.
MP4_FORMAT_NAMES = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})

#: The only AAC profile mainstream native players reliably decode. ffprobe
#: spells it "LC"; the file that started all this said "HE-AAC", which players
#: either refuse or fall back on.
AAC_LC_PROFILE = "LC"

#: H.264 encoders in preference order.
#:
#: libx264 is the better encoder and the obvious first choice -- but it is GPL,
#: so an LGPL FFmpeg build cannot contain it, and the build this application
#: installs for its own users is LGPL precisely so it can be redistributed.
#: That build ships Cisco's BSD-licensed libopenh264 instead. Hard-coding
#: libx264 would therefore have failed for every user who took the managed
#: install, which is the primary route on Windows. Hardware encoders are
#: deliberately absent: they are not portable or deterministic.
H264_ENCODERS: tuple[str, ...] = ("libx264", "libopenh264")

#: CRF 20 at preset medium is the quality/time balance for a CPU encode that
#: may have to handle 4K: visually high quality without an encode that runs for
#: hours. libopenh264 supports neither option and is driven by bitrate instead.
VIDEO_CRF = "20"
VIDEO_PRESET = "medium"

#: H.264 needs more bits than VP9 or AV1 for the same picture, so a straight
#: copy of the source bitrate would lose quality visibly. Doubling it, inside a
#: sane band, keeps the result close to the source without producing absurd
#: files. Only used for encoders that cannot be given a quality target.
BITRATE_MULTIPLIER = 2
MIN_VIDEO_BITRATE = 1_000_000
MAX_VIDEO_BITRATE = 24_000_000
FALLBACK_VIDEO_BITRATE = 6_000_000

AUDIO_ENCODER = "aac"
AUDIO_BITRATE = "192k"


class StreamAction(str, Enum):
    """What has to happen to one stream."""

    ABSENT = "absent"
    COPY = "copy"
    TRANSCODE = "transcode"


@dataclass(frozen=True)
class StreamInfo:
    """One stream, as ffprobe describes it."""

    codec: str | None
    profile: str | None = None
    pix_fmt: str | None = None


@dataclass(frozen=True)
class MediaProbe:
    """A file's shape, taken from ffprobe's JSON rather than parsed from text."""

    container: tuple[str, ...]
    video: StreamInfo | None
    audio: StreamInfo | None
    #: Bits per second, when ffprobe reports one. Used only to aim an encoder
    #: that takes no quality target.
    video_bitrate: int | None = None

    @classmethod
    def from_ffprobe(cls, data: Mapping[str, Any]) -> MediaProbe:
        """Build from ``ffprobe -show_streams -show_format -print_format json``."""
        video: StreamInfo | None = None
        audio: StreamInfo | None = None

        streams: Sequence[Mapping[str, Any]] = data.get("streams") or ()
        for stream in streams:
            kind = stream.get("codec_type")
            codec = stream.get("codec_name")
            if kind == "video" and video is None:
                # Cover art is a video stream to ffprobe but not to a player.
                if stream.get("disposition", {}).get("attached_pic"):
                    continue
                video = StreamInfo(
                    codec=str(codec) if codec else None,
                    profile=_text(stream.get("profile")),
                    pix_fmt=_text(stream.get("pix_fmt")),
                )
            elif kind == "audio" and audio is None:
                audio = StreamInfo(
                    codec=str(codec) if codec else None,
                    profile=_text(stream.get("profile")),
                )

        container_data = data.get("format") or {}
        raw_format = str(container_data.get("format_name") or "")
        container = tuple(part.strip() for part in raw_format.split(",") if part.strip())

        # The video stream's own rate where ffprobe reports one, else the
        # container's. Absent for plenty of files, hence optional.
        bitrate = next(
            (
                _integer(stream.get("bit_rate"))
                for stream in streams
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        if bitrate is None:
            bitrate = _integer(container_data.get("bit_rate"))

        return cls(container=container, video=video, audio=audio, video_bitrate=bitrate)

    @property
    def is_mp4(self) -> bool:
        return bool(MP4_FORMAT_NAMES.intersection(self.container))


def _integer(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    """ffprobe omits keys entirely rather than sending nulls -- Opus carries no
    ``profile`` at all, so absent has to mean absent and not "bad"."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# -- judging one stream --------------------------------------------------


def video_is_universal(video: StreamInfo | None) -> bool:
    """H.264 in 8-bit 4:2:0, which is what native players decode.

    An unknown pixel format is accepted rather than assumed wrong: ffprobe
    normally reports one, and refusing to trust a missing field would mean
    re-encoding a perfectly good file for no reason.
    """
    if video is None:
        return True  # nothing to be incompatible
    if video.codec != UNIVERSAL_VIDEO_CODEC:
        return False
    return video.pix_fmt in (None, UNIVERSAL_PIX_FMT)


def audio_is_universal(audio: StreamInfo | None) -> bool:
    """AAC, and where a profile is reported, Low Complexity.

    HE-AAC is the case that caused this: still "aac" to ffprobe, still in an
    MP4, and still not something an iPhone plays as an ordinary video. A stream
    with no profile at all -- Opus reports none -- is judged on its codec.
    """
    if audio is None:
        return True
    if audio.codec != UNIVERSAL_AUDIO_CODEC:
        return False
    if audio.profile is None:
        return True
    return audio.profile.strip().upper() == AAC_LC_PROFILE


# -- the plan ------------------------------------------------------------


@dataclass(frozen=True)
class CompatibilityPlan:
    """What has to be done to reach the universal target, and why."""

    video: StreamAction
    audio: StreamAction
    container_ok: bool
    source_video_codec: str | None
    source_audio_codec: str | None
    source_audio_profile: str | None

    @property
    def transcodes_anything(self) -> bool:
        return StreamAction.TRANSCODE in {self.video, self.audio}

    @property
    def action(self) -> str:
        """A short, stable label for diagnostics."""
        video = self.video is StreamAction.TRANSCODE
        audio = self.audio is StreamAction.TRANSCODE
        if video and audio:
            return "transcode_video_and_audio"
        if video:
            return "transcode_video"
        if audio:
            return "transcode_audio"
        return "stream_copy"

    @property
    def reason(self) -> str:
        if not self.transcodes_anything:
            return "already_universal"
        parts = []
        if self.video is StreamAction.TRANSCODE:
            parts.append(f"video={self.source_video_codec or 'unknown'}")
        if self.audio is StreamAction.TRANSCODE:
            profile = self.source_audio_profile
            codec = self.source_audio_codec or "unknown"
            parts.append(f"audio={codec}{f'/{profile}' if profile else ''}")
        return "incompatible:" + ",".join(parts)


def plan_for(probe: MediaProbe) -> CompatibilityPlan:
    """Decide the cheapest route from ``probe`` to the universal target.

    Only what is actually incompatible gets re-encoded. A file that is already
    H.264 + AAC-LC is copied, not encoded again: re-encoding it would cost time
    and a generation of quality for no gain.
    """
    return CompatibilityPlan(
        video=(
            StreamAction.ABSENT
            if probe.video is None
            else StreamAction.COPY
            if video_is_universal(probe.video)
            else StreamAction.TRANSCODE
        ),
        audio=(
            StreamAction.ABSENT
            if probe.audio is None
            else StreamAction.COPY
            if audio_is_universal(probe.audio)
            else StreamAction.TRANSCODE
        ),
        container_ok=probe.is_mp4,
        source_video_codec=probe.video.codec if probe.video else None,
        source_audio_codec=probe.audio.codec if probe.audio else None,
        source_audio_profile=probe.audio.profile if probe.audio else None,
    )


def parse_available_encoders(listing: str) -> frozenset[str]:
    """The encoder names in ``ffmpeg -encoders`` output.

    A narrow, tested reading of one well-defined listing rather than open-ended
    scraping: each entry is a six-character flags column followed by the name.
    It exists because which H.264 encoder is present depends on how FFmpeg was
    licensed, and only the binary can answer that.
    """
    found: set[str] = set()
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 2 or len(parts[0]) != 6 or parts[0][0] not in "VAS":
            continue
        name = parts[1]
        # The legend line reads " V..... = Video", which otherwise contributes
        # "=" as though it were an encoder.
        if name.replace("_", "").replace("-", "").isalnum():
            found.add(name)
    return frozenset(found)


def choose_video_encoder(available: frozenset[str]) -> str | None:
    """The best H.264 encoder this FFmpeg actually has, if any."""
    for name in H264_ENCODERS:
        if name in available:
            return name
    return None


def target_video_bitrate(source_bitrate: int | None) -> int:
    """What to aim an encoder that accepts no quality target."""
    if source_bitrate is None or source_bitrate <= 0:
        return FALLBACK_VIDEO_BITRATE
    return max(MIN_VIDEO_BITRATE, min(source_bitrate * BITRATE_MULTIPLIER, MAX_VIDEO_BITRATE))


def ffmpeg_output_arguments(
    plan: CompatibilityPlan,
    *,
    video_encoder: str = H264_ENCODERS[0],
    source_bitrate: int | None = None,
) -> list[str]:
    """The output side of the FFmpeg call for ``plan``.

    An argument list, never a command string, so nothing is exposed to a shell.
    Even a pure copy is remuxed: it costs no quality and it is the only way to
    guarantee faststart, which is what lets a player start before the whole
    file has arrived.
    """
    args: list[str] = []

    if plan.video is StreamAction.TRANSCODE:
        args += ["-c:v", video_encoder]
        if video_encoder == "libx264":
            # A quality target beats a bitrate: the encoder decides how many
            # bits each scene deserves.
            args += ["-crf", VIDEO_CRF, "-preset", VIDEO_PRESET]
        else:
            # libopenh264 understands neither -crf nor -preset.
            args += ["-b:v", str(target_video_bitrate(source_bitrate))]
        args += ["-pix_fmt", UNIVERSAL_PIX_FMT]
    elif plan.video is StreamAction.COPY:
        args += ["-c:v", "copy"]

    if plan.audio is StreamAction.TRANSCODE:
        args += ["-c:a", AUDIO_ENCODER, "-b:a", AUDIO_BITRATE]
    elif plan.audio is StreamAction.COPY:
        args += ["-c:a", "copy"]
    else:
        args += ["-an"]

    # Subtitles and data streams are dropped rather than carried into MP4,
    # where an unsupported one fails the mux outright.
    args += ["-sn", "-dn", "-map_metadata", "0", "-movflags", "+faststart", "-f", "mp4"]
    return args


# -- judging the result --------------------------------------------------


@dataclass(frozen=True)
class CompatibilityVerdict:
    """Whether the file we produced actually meets the target."""

    ok: bool
    problems: tuple[str, ...]
    video_codec: str | None
    audio_codec: str | None
    audio_profile: str | None
    pix_fmt: str | None
    container: str

    def as_log_fields(self) -> str:
        """The completion record, bounded and free of anything sensitive."""
        fields = [
            f"final_video_codec={self.video_codec or 'none'}",
            f"final_audio_codec={self.audio_codec or 'none'}",
        ]
        if self.audio_profile:
            fields.append(f"final_audio_profile={self.audio_profile}")
        fields.append(f"final_container={self.container}")
        return " ".join(fields)


def validate_universal(probe: MediaProbe) -> CompatibilityVerdict:
    """Judge a finished file against the universal target.

    Deliberately checks the file rather than trusting FFmpeg's exit code: a
    command can succeed and still produce something a phone will not play,
    which is precisely the failure this whole module exists to prevent.
    """
    problems: list[str] = []

    if probe.video is None:
        problems.append("no video stream")
    elif probe.video.codec != UNIVERSAL_VIDEO_CODEC:
        problems.append(f"video codec is {probe.video.codec or 'unknown'}, expected h264")
    elif probe.video.pix_fmt not in (None, UNIVERSAL_PIX_FMT):
        problems.append(f"pixel format is {probe.video.pix_fmt}, expected yuv420p")

    if probe.audio is not None:
        if probe.audio.codec != UNIVERSAL_AUDIO_CODEC:
            problems.append(f"audio codec is {probe.audio.codec or 'unknown'}, expected aac")
        elif probe.audio.profile and probe.audio.profile.strip().upper() != AAC_LC_PROFILE:
            problems.append(f"audio profile is {probe.audio.profile}, expected LC")

    if not probe.is_mp4:
        problems.append(f"container is {','.join(probe.container) or 'unknown'}, expected mp4")

    return CompatibilityVerdict(
        ok=not problems,
        problems=tuple(problems),
        video_codec=probe.video.codec if probe.video else None,
        audio_codec=probe.audio.codec if probe.audio else None,
        audio_profile=probe.audio.profile if probe.audio else None,
        pix_fmt=probe.video.pix_fmt if probe.video else None,
        container=UNIVERSAL_CONTAINER if probe.is_mp4 else (",".join(probe.container) or "unknown"),
    )
