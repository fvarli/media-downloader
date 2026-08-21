"""The yt-dlp wrapper.

yt-dlp is used as a *library*, never as a subprocess. Nothing here builds a
command line, so no user-supplied URL is ever exposed to a shell and there are
no quoting differences between POSIX shells and cmd.exe. yt-dlp invokes FFmpeg
itself, with an argument list, which stays yt-dlp's responsibility.

``ydl_factory`` is injectable so the whole class can be exercised in tests
against a fake, with no network access.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from media_downloader.config import DownloadRequest
from media_downloader.errors import (
    DownloadFailedError,
    MediaUnavailableError,
    OutputError,
)
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.logging_setup import get_logger
from media_downloader.naming import ensure_output_dir
from media_downloader.options import build_info_opts, build_ydl_opts

logger = get_logger("downloader")

# Substrings yt-dlp puts in DownloadError messages when media exists but is not
# publicly accessible. These map to a distinct exit code so scripts can tell
# "not allowed" apart from "network problem".
_UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "private video",
    "this video is private",
    "sign in to confirm",
    "login required",
    "requested format is not available",
    "video unavailable",
    "removed by the uploader",
    "account has been terminated",
    "age-restricted",
    "age restricted",
    "confirm your age",
    "available in your country",
    "geo restricted",
    "geo-restricted",
    "blocked it in your country",
    "drm",
    "protected by drm",
    "this post is unavailable",
    "no longer exists",
)


class YoutubeDLLike(Protocol):
    """The slice of ``yt_dlp.YoutubeDL`` this project actually uses."""

    def extract_info(self, url: str, download: bool = ...) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


YDLFactory = Callable[[dict[str, Any]], YoutubeDLLike]


@dataclass(frozen=True)
class MediaInfo:
    """The metadata subset shown by ``--info``."""

    title: str
    uploader: str | None
    duration_seconds: float | None
    extractor: str
    webpage_url: str
    width: int | None = None
    height: int | None = None
    filesize_bytes: int | None = None
    ext: str | None = None

    @classmethod
    def from_info_dict(cls, info: dict[str, Any]) -> MediaInfo:
        return cls(
            title=str(info.get("title") or "Unknown title"),
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            duration_seconds=info.get("duration"),
            extractor=str(info.get("extractor_key") or info.get("extractor") or "unknown"),
            webpage_url=str(info.get("webpage_url") or info.get("original_url") or ""),
            width=info.get("width"),
            height=info.get("height"),
            filesize_bytes=info.get("filesize") or info.get("filesize_approx"),
            ext=info.get("ext"),
        )


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a successful download."""

    path: Path
    info: MediaInfo


def _default_factory(opts: dict[str, Any]) -> YoutubeDLLike:
    """Construct a real ``yt_dlp.YoutubeDL``.

    Imported lazily so that ``--help`` and argument errors do not pay for
    yt-dlp's fairly heavy import.
    """
    from yt_dlp import YoutubeDL

    return YoutubeDL(opts)  # type: ignore[no-any-return]


def _classify_download_error(exc: Exception) -> Exception:
    """Translate a yt-dlp error into this project's exception hierarchy."""
    message = str(exc)
    haystack = message.lower()

    if any(marker in haystack for marker in _UNAVAILABLE_MARKERS):
        return MediaUnavailableError(
            f"This media is not publicly accessible: {message}",
            hint=(
                "It may be private, removed, age-restricted, region-locked or "
                "DRM-protected. This tool only downloads publicly accessible "
                "media and does not bypass access controls."
            ),
        )

    return DownloadFailedError(
        f"The download failed: {message}",
        hint=(
            "If the site recently changed, updating the extractor usually "
            "fixes it: pip install -U yt-dlp"
        ),
    )


class Downloader:
    """Runs metadata lookups and downloads through yt-dlp."""

    def __init__(
        self,
        ffmpeg: FFmpegStatus,
        *,
        js_runtime: JSRuntimeStatus | None = None,
        ydl_factory: YDLFactory | None = None,
        progress_hook: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._js_runtime = js_runtime
        self._factory: YDLFactory = ydl_factory or _default_factory
        self._progress_hook = progress_hook
        self._verbose = verbose

    @contextmanager
    def _session(self, opts: dict[str, Any]) -> Iterator[YoutubeDLLike]:
        ydl = self._factory(opts)
        try:
            yield ydl
        finally:
            close = getattr(ydl, "close", None)
            if callable(close):
                close()

    def _extract(self, opts: dict[str, Any], url: str, *, download: bool) -> dict[str, Any]:
        """Run yt-dlp and normalise both its errors and its return value."""
        from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

        try:
            with self._session(opts) as ydl:
                info = ydl.extract_info(url, download=download)
        except UnsupportedError as exc:
            raise DownloadFailedError(
                f"yt-dlp has no extractor for this URL: {url}",
                hint="Check the URL, or see the yt-dlp supported-sites list.",
            ) from exc
        except (DownloadError, ExtractorError) as exc:
            raise _classify_download_error(exc) from exc
        except OSError as exc:
            raise OutputError(f"A filesystem error occurred: {exc}") from exc

        if info is None:
            raise DownloadFailedError("yt-dlp returned no information for this URL.")

        # A URL that resolves to a playlist despite noplaylist=True.
        entries = info.get("entries")
        if entries:
            first = next((entry for entry in entries if entry), None)
            if first is None:
                raise DownloadFailedError("This URL contains no downloadable media.")
            logger.info("URL resolved to a collection; using the first item.")
            info = first

        return info

    def fetch_info(self, request: DownloadRequest) -> MediaInfo:
        """Retrieve metadata without downloading anything."""
        opts = build_info_opts(request, js_runtime=self._js_runtime, quiet=not self._verbose)
        info = self._extract(opts, request.url, download=False)
        return MediaInfo.from_info_dict(info)

    def download(self, request: DownloadRequest) -> DownloadResult:
        """Download the media and return its final path on disk."""
        ensure_output_dir(request.output_dir)

        final_paths: list[str] = []
        opts = build_ydl_opts(
            request,
            self._ffmpeg,
            js_runtime=self._js_runtime,
            progress_hooks=[self._progress_hook] if self._progress_hook else None,
            # post_hooks fire with the final path after every postprocessor and
            # file move, so this is the only reliable source for the result.
            post_hooks=[final_paths.append],
            quiet=not self._verbose,
        )

        info = self._extract(opts, request.url, download=True)
        path = self._resolve_final_path(info, final_paths, request)
        return DownloadResult(path=path, info=MediaInfo.from_info_dict(info))

    @staticmethod
    def _resolve_final_path(
        info: dict[str, Any],
        post_hook_paths: list[str],
        request: DownloadRequest,
    ) -> Path:
        """Determine where the file actually landed.

        Postprocessing renames files, so the name is read from yt-dlp rather
        than reconstructed from the output template.
        """
        if post_hook_paths:
            return Path(post_hook_paths[-1]).resolve()

        for download in info.get("requested_downloads") or []:
            filepath = download.get("filepath") or download.get("_filename")
            if filepath:
                return Path(filepath).resolve()

        filepath = info.get("filepath") or info.get("_filename")
        if filepath:
            return Path(filepath).resolve()

        raise OutputError(
            "The download reported success but no output file could be located.",
            hint=f"Check the output directory: {request.output_dir}",
        )
