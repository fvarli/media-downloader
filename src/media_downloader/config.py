"""Request configuration and the CLI > environment > default precedence chain."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from media_downloader.naming import (
    resolve_output_dir,
    validate_filename_template,
)

ENV_OUTPUT_DIR = "MEDIA_DOWNLOADER_OUTPUT"
ENV_FFMPEG_LOCATION = "MEDIA_DOWNLOADER_FFMPEG"

DEFAULT_OUTPUT_DIRNAME = "downloads"

QUALITY_CHOICES: tuple[str, ...] = (
    "best",
    "2160",
    "1440",
    "1080",
    "720",
    "480",
    "360",
    "worst",
)

# "best" means "keep the original audio stream", which needs no re-encoding and
# therefore no FFmpeg. Every other value is a conversion and does require it.
AUDIO_FORMAT_CHOICES: tuple[str, ...] = ("best", "mp3", "m4a", "opus", "flac", "wav")
LOSSLESS_AUDIO_FORMAT = "best"


@dataclass(frozen=True)
class DownloadRequest:
    """A fully resolved description of one download.

    Built once by the CLI and then passed unchanged through the rest of the
    application, so no other module has to consult ``sys.argv`` or the
    environment.
    """

    url: str
    output_dir: Path
    quality: str = "best"
    audio_only: bool = False
    audio_format: str = LOSSLESS_AUDIO_FORMAT
    # None means the user gave no --filename, so the automatic naming policy
    # in media_downloader.naming applies. A string is the user's own template
    # and is never rewritten beyond the existing safety validation.
    filename_template: str | None = None
    ffmpeg_location: str | None = None
    info_only: bool = False
    overwrite: bool = False
    extra_ydl_opts: Mapping[str, object] = field(default_factory=dict)

    @property
    def needs_audio_conversion(self) -> bool:
        """True when the user asked for a specific audio codec.

        This is the case that must fail loudly without FFmpeg, as opposed to a
        plain ``--audio`` download, which can simply keep the original stream.
        """
        return self.audio_only and self.audio_format != LOSSLESS_AUDIO_FORMAT


def default_output_dir() -> Path:
    """The download directory used when neither flag nor env var is set."""
    return Path.cwd() / DEFAULT_OUTPUT_DIRNAME


def resolve_setting(
    cli_value: str | None,
    env_var: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Apply the CLI > environment > unset precedence chain for one setting."""
    if cli_value is not None:
        return cli_value

    source = os.environ if env is None else env
    env_value = source.get(env_var, "").strip()
    return env_value or None


def resolve_output_setting(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the output directory from CLI, environment, or the default."""
    chosen = resolve_setting(cli_value, ENV_OUTPUT_DIR, env)
    if chosen is None:
        return default_output_dir()
    return resolve_output_dir(chosen)


def build_request(
    *,
    url: str,
    output: str | None = None,
    quality: str = "best",
    audio_only: bool = False,
    audio_format: str = LOSSLESS_AUDIO_FORMAT,
    filename: str | None = None,
    ffmpeg_location: str | None = None,
    info_only: bool = False,
    overwrite: bool = False,
    env: Mapping[str, str] | None = None,
) -> DownloadRequest:
    """Assemble a validated :class:`DownloadRequest` from raw CLI values."""
    template = validate_filename_template(filename) if filename is not None else None

    return DownloadRequest(
        url=url,
        output_dir=resolve_output_setting(output, env),
        quality=quality,
        audio_only=audio_only,
        audio_format=audio_format,
        filename_template=template,
        ffmpeg_location=resolve_setting(ffmpeg_location, ENV_FFMPEG_LOCATION, env),
        info_only=info_only,
        overwrite=overwrite,
    )
