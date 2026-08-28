"""Exception hierarchy and process exit codes.

Every failure the CLI can report is modelled as a :class:`MediaDownloaderError`
subclass carrying its own :class:`ExitCode`, so ``cli.main`` never has to map
error types to numbers by hand.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes returned by the CLI."""

    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    USAGE_ERROR = 2
    INVALID_URL = 3
    FFMPEG_REQUIRED = 4
    DOWNLOAD_FAILED = 5
    MEDIA_UNAVAILABLE = 6
    OUTPUT_ERROR = 7
    INTERRUPTED = 130


class MediaDownloaderError(Exception):
    """Base class for every error this application reports deliberately.

    ``hint`` holds an optional second line of user-facing guidance, kept
    separate from the message so the CLI can style the two differently.
    """

    exit_code: ExitCode = ExitCode.UNEXPECTED_ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class InvalidURLError(MediaDownloaderError):
    """The supplied string is not a usable public http(s) media URL."""

    exit_code = ExitCode.INVALID_URL


class FFmpegRequiredError(MediaDownloaderError):
    """An explicitly requested operation needs FFmpeg, which was not found."""

    exit_code = ExitCode.FFMPEG_REQUIRED


class DownloadFailedError(MediaDownloaderError):
    """yt-dlp could not extract or download the media (network, extractor)."""

    exit_code = ExitCode.DOWNLOAD_FAILED


class NoFormatMatchError(DownloadFailedError):
    """No available format satisfied the requested quality and options.

    Deliberately separate from MediaUnavailableError. yt-dlp saying "Requested
    format is not available" is a statement about *our* selection, not about
    the media's accessibility -- and reporting it as private, removed,
    age-restricted, region-locked or DRM-protected told a user something
    untrue about a video that was none of those things.

    Subclasses DownloadFailedError so the process exit code is unchanged.
    """


class CompatibilityConversionError(DownloadFailedError):
    """The media downloaded, but could not be made universally playable.

    Kept apart from a plain download failure because the outcome on disk is
    different and the user can see it: the file is there and it plays in VLC,
    so "the download failed" reads as nonsense. What actually failed is the
    conversion, and the file that survived is the untouched source -- which is
    exactly what Original mode would have produced deliberately.

    Subclasses DownloadFailedError so the process exit code is unchanged.
    """


class MediaUnavailableError(MediaDownloaderError):
    """The media exists but is not publicly accessible to this user.

    Covers private, removed, age-gated, geo-blocked, login-required and
    DRM-protected media. The application never attempts to circumvent any of
    these restrictions.
    """

    exit_code = ExitCode.MEDIA_UNAVAILABLE


class ToolInstallError(MediaDownloaderError):
    """An install could not be completed. Nothing was left behind.

    Defined here with the rest of the hierarchy rather than beside the
    installer, so the HTTPS layer and the tool manager can both raise it
    without importing each other.
    """


class OutputError(MediaDownloaderError):
    """The output directory or filename template cannot be used."""

    exit_code = ExitCode.OUTPUT_ERROR
