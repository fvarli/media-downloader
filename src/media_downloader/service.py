"""Application layer shared by the CLI and the local web UI.

Both front ends need the same sequence: work out which external tools are
available, explain what their absence will cost, and build a
:class:`~media_downloader.downloader.Downloader` around that. Keeping it here
is what makes "one downloader implementation" structural rather than a
convention -- neither front end reaches past this module to assemble its own.

Nothing here renders anything. Degradations are returned as :class:`Notice`
values so the CLI can print them with Rich and the web UI can serialise them
as JSON, from a single source of truth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from media_downloader.config import DownloadRequest
from media_downloader.downloader import Downloader
from media_downloader.ffmpeg import FFMPEG_GUIDANCE, FFmpegStatus, detect_ffmpeg
from media_downloader.jsruntime import JS_RUNTIME_GUIDANCE, JSRuntimeStatus, detect_js_runtime
from media_downloader.urls import detect_service

NoticeLevel = Literal["warning", "info"]


@dataclass(frozen=True)
class Notice:
    """A user-facing note about degraded capability.

    ``level`` is presentation-neutral on purpose: the CLI maps it to a Rich
    style, the web UI to a CSS class.
    """

    level: NoticeLevel
    message: str


@dataclass(frozen=True)
class Environment:
    """External tools available to this process."""

    ffmpeg: FFmpegStatus
    js_runtime: JSRuntimeStatus


def detect_environment(ffmpeg_location: str | None = None) -> Environment:
    """Probe for FFmpeg and a JavaScript runtime."""
    return Environment(ffmpeg=detect_ffmpeg(ffmpeg_location), js_runtime=detect_js_runtime())


def environment_notices(request: DownloadRequest, env: Environment) -> list[Notice]:
    """Explain, in plain language, what any missing tool will cost.

    Returns an empty list when everything needed is present.
    """
    notices: list[Notice] = []

    if not env.ffmpeg.available:
        notices.append(Notice("warning", FFMPEG_GUIDANCE))
        # An explicit conversion request is about to be refused outright, so
        # promising to continue would contradict the error that follows.
        if not request.needs_audio_conversion:
            fallback = (
                "the original audio stream will be saved as-is, without conversion."
                if request.audio_only
                else "only pre-merged formats will be used, so the available "
                "quality may be lower than usual."
            )
            notices.append(Notice("info", f"Continuing: {fallback}"))

    service = detect_service(request.url)
    if service is not None and service.key == "youtube" and not env.js_runtime.available:
        notices.append(Notice("warning", JS_RUNTIME_GUIDANCE))

    return notices


def create_downloader(
    env: Environment,
    *,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    verbose: bool = False,
) -> Downloader:
    """Build a :class:`Downloader` for ``env``.

    The hooks are how a front end observes progress; everything else about the
    download is identical whichever front end asked for it.
    """
    return Downloader(
        env.ffmpeg,
        js_runtime=env.js_runtime,
        progress_hook=progress_hook,
        postprocessor_hook=postprocessor_hook,
        verbose=verbose,
    )
