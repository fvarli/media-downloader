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
from media_downloader.naming import AUTO_OUTPUT_TEMPLATE

# Best video plus best audio, falling back to the best single stream.
FORMAT_BEST = "bv*+ba/b"
FORMAT_WORST = "wv*+wa/w"
# Progressive only: a single file that already contains both video and audio,
# so nothing has to be merged. Used when FFmpeg is unavailable.
FORMAT_PROGRESSIVE = "b"
FORMAT_BEST_AUDIO = "ba/b"

#: Resolution and frame rate decide first; a compatible codec is only a
#: tie-breaker. So a 2160p H.264 stream wins over 2160p VP9 -- a free remux
#: instead of an encode -- while 2160p that exists *only* as VP9 still beats
#: 1080p H.264. Quality is never traded away to avoid a transcode; the file is
#: normalised afterwards instead. Verified against yt-dlp's own FormatSorter.
UNIVERSAL_FORMAT_SORT: tuple[str, ...] = ("res", "fps", "vcodec:h264", "acodec:aac")


def build_format_selector(quality: str, *, ffmpeg_available: bool) -> str:
    """Build the yt-dlp format selector for a video download.

    Without FFmpeg, separate video and audio streams cannot be merged, so the
    selector is restricted to progressive formats. This keeps the download
    working at reduced quality instead of failing at the merge step.
    """
    if not ffmpeg_available:
        if quality in {"best", "worst"}:
            return FORMAT_PROGRESSIVE if quality == "best" else "w"
        return f"b[height<=?{quality}]/b"

    if quality == "best":
        return FORMAT_BEST
    if quality == "worst":
        return FORMAT_WORST
    return f"bv*[height<=?{quality}]+ba/b[height<=?{quality}]/b"


def build_audio_postprocessors(audio_format: str) -> list[dict[str, Any]]:
    """Postprocessor chain for ``--audio``.

    ``best`` asks FFmpeg to keep the source codec and only move it into a
    suitable container, so no quality is lost to re-encoding.
    """
    # preferredquality is deliberately omitted: yt-dlp then derives the bitrate
    # from the source stream instead of forcing a fixed one.
    return [{"key": "FFmpegExtractAudio", "preferredcodec": audio_format}]


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
