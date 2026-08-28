"""Translation of a :class:`DownloadRequest` into yt-dlp options.

This module is pure: same inputs, same dictionary, no I/O and no globals. That
is what makes the trickiest part of the project -- format selection and the
FFmpeg fallbacks -- straightforward to unit-test without a network.
"""

from __future__ import annotations

from typing import Any

from media_downloader.config import DownloadRequest
from media_downloader.errors import FFmpegRequiredError
from media_downloader.ffmpeg import FFMPEG_GUIDANCE, FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus, js_runtimes_option
from media_downloader.logging_setup import get_logger
from media_downloader.naming import AUTO_OUTPUT_TEMPLATE

# Best video plus best audio, falling back to the best single stream.
#: A chain, not one selector. Every step is tried in order and the first that
#: matches wins.
#:
#: The old chain ended at ``b`` -- a single format holding both video and audio
#: -- which sounds like a safety net and is not one on YouTube: measured
#: against real videos, it returns *zero* muxed formats. So the chain collapsed
#: to "video plus audio, or fail", and one missing audio stream was fatal even
#: though a perfectly good video stream was sitting there.
logger = get_logger("ytdlp")

FORMAT_BEST_AUDIO = "ba/b"

#: Resolution and frame rate decide first; a compatible codec is only a
#: tie-breaker. So a 2160p H.264 stream wins over 2160p VP9 -- a free remux
#: instead of an encode -- while 2160p that exists *only* as VP9 still beats
#: 1080p H.264. This is a sort, never a filter: it changes the order of the
#: candidates and can never make one unavailable.
UNIVERSAL_FORMAT_SORT: tuple[str, ...] = ("res", "fps", "vcodec:h264", "acodec:aac")

#: Video and its audio, merged. The normal path everywhere.
_VIDEO_WITH_AUDIO = "bv*+ba"
_WORST_VIDEO_WITH_AUDIO = "wv*+wa"
#: One file already containing both. Rare on YouTube, ordinary elsewhere.
_MUXED = "b"
_WORST_MUXED = "w"
#: Video alone. A silent file is a poor result, but it is a result, and the
#: user is told plainly that it has no sound rather than left to discover it.
_VIDEO_ONLY = "bv*"
_WORST_VIDEO_ONLY = "wv*"


def _capped(selector: str, height: str) -> str:
    """Apply a height cap. ``<=?`` keeps formats that report no height at all."""
    if "+" in selector:
        video, audio = selector.split("+", 1)
        return f"{video}[height<=?{height}]+{audio}"
    return f"{selector}[height<=?{height}]"


def format_selector_steps(quality: str, *, ffmpeg_available: bool) -> tuple[str, ...]:
    """The ordered candidates for a video download.

    Without FFmpeg nothing can be merged, so the steps that would need a merge
    are simply absent rather than offered and then failed at.
    """
    merged = [_VIDEO_WITH_AUDIO] if ffmpeg_available else []
    worst_merged = [_WORST_VIDEO_WITH_AUDIO] if ffmpeg_available else []

    if quality == "best":
        return (*merged, _MUXED, _VIDEO_ONLY)
    if quality == "worst":
        return (*worst_merged, _WORST_MUXED, _WORST_VIDEO_ONLY)

    # A cap is an upper bound first: asking for 1080 where only 720 exists
    # gives 720. Where nothing at all fits under the cap, the smallest
    # available stream is taken rather than refusing outright -- somebody who
    # asked for 360p wants a small file, not an error -- and they are told the
    # cap could not be honoured.
    capped = [_capped(step, quality) for step in (*merged, _MUXED, _VIDEO_ONLY)]
    return (*capped, *worst_merged, _WORST_MUXED, _WORST_VIDEO_ONLY)


def build_format_selector(quality: str, *, ffmpeg_available: bool) -> str:
    """Build the yt-dlp format selector for a video download."""
    return "/".join(format_selector_steps(quality, ffmpeg_available=ffmpeg_available))


def build_audio_postprocessors(audio_format: str) -> list[dict[str, Any]]:
    """Postprocessor chain for ``--audio``.

    ``best`` asks FFmpeg to keep the source codec and only move it into a
    suitable container, so no quality is lost to re-encoding.
    """
    # preferredquality is deliberately omitted: yt-dlp then derives the bitrate
    # from the source stream instead of forcing a fixed one.
    return [{"key": "FFmpegExtractAudio", "preferredcodec": audio_format}]


class _YtdlpLog:
    """Routes yt-dlp's own messages into our log.

    Without this they go to stderr, which a windowed build discards -- so when
    a real download failed with "Requested format is not available", the one
    thing that would have explained *why* formats were unusable had already
    been thrown away. Warnings are the valuable half: yt-dlp says there when it
    skips formats and what forced it.

    Everything passes through the same redacting filter as the rest of the log.
    """

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def debug(self, message: str) -> None:
        # yt-dlp routes ordinary output here too, prefixed when it is genuinely
        # debug. Only the noisy half is dropped.
        if message.startswith("[debug] "):
            self._logger.debug("yt-dlp %s", message[8:])
        else:
            self._logger.debug("yt-dlp %s", message)

    def info(self, message: str) -> None:
        self._logger.debug("yt-dlp %s", message)

    def warning(self, message: str) -> None:
        self._logger.warning("yt-dlp %s", message)

    def error(self, message: str) -> None:
        self._logger.error("yt-dlp %s", message)


#: Stateless, so one instance is shared. Building it per call would make
#: build_ydl_opts impure -- the options dict would differ between two calls
#: with identical inputs, which a test rightly objects to.
_YTDLP_LOG = _YtdlpLog(logger)


def build_ydl_opts(
    request: DownloadRequest,
    ffmpeg: FFmpegStatus,
    *,
    js_runtime: JSRuntimeStatus | None = None,
    progress_hooks: list[Any] | None = None,
    post_hooks: list[Any] | None = None,
    postprocessor_hooks: list[Any] | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    """Build the complete yt-dlp options dictionary for ``request``.

    Raises:
        FFmpegRequiredError: if the request explicitly asks for audio
            conversion but FFmpeg is unavailable.
    """
    if request.needs_universal_video and not ffmpeg.available:
        # Universal is a promise that the output plays natively, and without
        # ffprobe there is no way to check what was produced. Claiming it
        # anyway is how an MP4 full of VP9 reached somebody's phone.
        raise FFmpegRequiredError(
            "Universal compatibility requires FFmpeg.",
            hint=(
                f"{FFMPEG_GUIDANCE} Alternatively choose original quality, which "
                "keeps the source codecs and needs no conversion -- but does not "
                "guarantee playback in native Apple or Windows players."
            ),
        )

    if request.needs_audio_conversion and not ffmpeg.available:
        raise FFmpegRequiredError(
            f"Converting audio to {request.audio_format} requires FFmpeg.",
            hint=(
                f"{FFMPEG_GUIDANCE} Alternatively run --audio without "
                "--audio-format to keep the original audio stream, which needs "
                "no conversion."
            ),
        )

    # With no --filename, the automatic template pulls in the cleaned name that
    # downloader._register_auto_naming injects before yt-dlp builds the name.
    # The template stays relative and the directory is supplied via "paths":
    # an absolute outtmpl would make yt-dlp ignore "paths" and scatter the
    # intermediate .part files. naming.validate_filename_template has already
    # guaranteed the template cannot contain a path separator.
    opts: dict[str, Any] = {
        "outtmpl": {"default": request.filename_template or AUTO_OUTPUT_TEMPLATE},
        "paths": {"home": str(request.output_dir)},
        # Applied on every OS so filenames are identical across platforms and
        # safe on FAT/exFAT/NTFS volumes. yt-dlp owns all sanitisation.
        "windowsfilenames": True,
        "trim_file_name": 200,
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": False,
        "noprogress": True,
        "consoletitle": False,
        # yt-dlp's own account of what it did, in our log rather than a
        # discarded stderr.
        "logger": _YTDLP_LOG,
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "overwrites": request.overwrite,
    }

    if request.audio_only:
        opts["format"] = FORMAT_BEST_AUDIO
        if ffmpeg.available:
            opts["postprocessors"] = build_audio_postprocessors(request.audio_format)
        # Without FFmpeg the original audio stream is simply saved as-is; the
        # constructor above has already rejected any request needing conversion.
    else:
        opts["format"] = build_format_selector(request.quality, ffmpeg_available=ffmpeg.available)
        if request.needs_universal_video:
            # A preference, not a filter: nothing becomes unavailable, the
            # already-compatible option is simply ranked first among equals.
            opts["format_sort"] = list(UNIVERSAL_FORMAT_SORT)

    if ffmpeg.available and ffmpeg.location is not None:
        opts["ffmpeg_location"] = str(ffmpeg.location)

    # yt-dlp enables Deno only. An installed Node or Bun has to be switched on
    # explicitly or it is ignored; solving the challenges remains yt-dlp's job.
    if js_runtime is not None:
        runtimes = js_runtimes_option(js_runtime)
        if runtimes is not None:
            opts["js_runtimes"] = runtimes

    if progress_hooks:
        opts["progress_hooks"] = progress_hooks
    if post_hooks:
        opts["post_hooks"] = post_hooks
    # Reports the FFmpeg merge and audio-conversion phase, which produces no
    # download progress events but can take a noticeable amount of time.
    if postprocessor_hooks:
        opts["postprocessor_hooks"] = postprocessor_hooks

    opts.update(request.extra_ydl_opts)
    return opts


def build_info_opts(
    request: DownloadRequest,
    *,
    js_runtime: JSRuntimeStatus | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    """Minimal options for a metadata-only lookup (``--info``)."""
    opts: dict[str, Any] = {
        "quiet": quiet,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,
        "noprogress": True,
    }

    if js_runtime is not None:
        runtimes = js_runtimes_option(js_runtime)
        if runtimes is not None:
            opts["js_runtimes"] = runtimes

    opts.update(request.extra_ydl_opts)
    return opts
