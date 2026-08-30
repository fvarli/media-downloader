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
    CompatibilityConversionError,
    DownloadFailedError,
    MediaUnavailableError,
    NoFormatMatchError,
    OutputError,
)
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.logging_setup import get_logger
from media_downloader.naming import AUTO_NAME_FIELD, build_auto_filename_stem, ensure_output_dir
from media_downloader.normalize import FINAL_AUDIO_CODEC_FIELD, register_universal_compatibility
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
    #: The extractor's own identifier for this media. Comes from metadata, so
    #: it can be logged without touching the source URL's query string.
    media_id: str | None
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
            media_id=str(info["id"]) if info.get("id") else None,
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            duration_seconds=info.get("duration"),
            extractor=str(info.get("extractor_key") or info.get("extractor") or "unknown"),
            webpage_url=str(info.get("webpage_url") or info.get("original_url") or ""),
            width=info.get("width"),
            height=info.get("height"),
            filesize_bytes=info.get("filesize") or info.get("filesize_approx"),
            ext=info.get("ext"),
        )


#: Sentinel: the file was never probed, which is different from probed and
#: silent. Original mode never probes, so its reporting stays metadata-derived.
_NOT_PROBED = object()


def _probed_audio_codec(info: dict[str, Any]) -> Any:
    """The audio codec ffprobe found in the produced file, if anything looked.

    Read from the per-download entry as well as the top level, because that is
    where a postprocessor's changes land -- yt-dlp collects the dict the
    postprocessor mutated into ``requested_downloads``.
    """
    for candidate in (info, *(info.get("requested_downloads") or [])):
        if FINAL_AUDIO_CODEC_FIELD in candidate:
            return candidate[FINAL_AUDIO_CODEC_FIELD]
    return _NOT_PROBED


@dataclass(frozen=True)
class SelectionOutcome:
    """Which branch of the format chain actually produced the file.

    Recorded rather than inferred later, because two of the branches are
    fallbacks the user deserves to know about: a file with no sound, and a
    quality cap that could not be honoured. Silence about either would be the
    kind of surprise this project keeps trying to avoid.
    """

    #: A short, stable label for diagnostics.
    selection: str
    has_audio: bool
    height: int | None
    requested_quality: str

    @property
    def cap_exceeded(self) -> bool:
        """True when a numeric cap was asked for and the result is above it."""
        if not self.requested_quality.isdigit() or self.height is None:
            return False
        return self.height > int(self.requested_quality)

    def notices(self) -> list[str]:
        """Plain sentences for the interface. Empty when nothing surprising
        happened, which is the ordinary case."""
        messages: list[str] = []
        if not self.has_audio:
            messages.append(
                "Downloaded video without audio, because no usable audio stream "
                "was available for this item."
            )
        if self.cap_exceeded:
            messages.append(
                f"{self.requested_quality}p was not available. Downloaded the "
                f"lowest available quality: {self.height}p."
            )
        return messages


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a successful download."""

    path: Path
    info: MediaInfo
    outcome: SelectionOutcome | None = None


def _make_auto_name_postprocessor() -> Any:
    """Build the postprocessor that injects the automatic filename stem.

    yt-dlp runs ``pre_process`` hooks immediately before it builds the output
    filename, so writing the cleaned name into the info dict there is enough --
    nothing is renamed afterwards, and the merge, audio-conversion and
    final-path steps all see the same name.

    Passing the name as a *field value* rather than splicing it into the
    template also means a title containing ``%`` or something that looks like
    ``%(id)s`` is never re-interpreted as a template.

    Imported lazily so ``--help`` does not pay for yt-dlp's import.
    """
    from yt_dlp.postprocessor import PostProcessor

    class AutoNamePP(PostProcessor):  # type: ignore[misc]
        """Writes the cleaned filename stem into the info dict."""

        def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
            try:
                stem = build_auto_filename_stem(info)
            except Exception:  # pragma: no cover - naming must never abort a download
                logger.debug("Automatic naming failed; falling back to the raw title.")
                return [], info
            if stem:
                info[AUTO_NAME_FIELD] = stem
            return [], info

    return AutoNamePP()


def _register_auto_naming(ydl: YoutubeDLLike) -> None:
    """Attach the automatic-naming postprocessor if the object supports it.

    Guarded so an injected test double without ``add_post_processor`` -- or a
    future yt-dlp that drops it -- degrades to yt-dlp's own naming instead of
    raising. The output template falls back to the raw title in that case.
    """
    add = getattr(ydl, "add_post_processor", None)
    if not callable(add):
        logger.debug("This yt-dlp object cannot take postprocessors; using default naming.")
        return
    try:
        add(_make_auto_name_postprocessor(), when="pre_process")
    except Exception:  # pragma: no cover - never let naming break a download
        logger.debug("Could not register automatic naming; using default naming.")


def _default_factory(opts: dict[str, Any]) -> YoutubeDLLike:
    """Construct a real ``yt_dlp.YoutubeDL``.

    Imported lazily so that ``--help`` and argument errors do not pay for
    yt-dlp's fairly heavy import.
    """
    from yt_dlp import YoutubeDL

    return YoutubeDL(opts)  # type: ignore[no-any-return]


#: yt-dlp's wording when the format selector matched nothing. It says nothing
#: about whether the media is accessible, and used to be read as though it did.
_NO_FORMAT_MARKERS: tuple[str, ...] = (
    "requested format is not available",
    "requested format not available",
)


#: yt-dlp prefixes anything raised by a postprocessor with this. By the time
#: one runs, the media is already downloaded and sitting on disk.
_POSTPROCESSING_MARKER = "postprocessing:"


def _classify_download_error(exc: Exception, *, universal: bool = False) -> Exception:
    """Translate a yt-dlp error into this project's exception hierarchy."""
    message = str(exc)
    haystack = message.lower()

    # Only for Universal video. Audio extraction is a postprocessor too, and
    # describing a failed MP3 conversion as a playback-compatibility problem
    # would be the same kind of confident wrong answer this phase is fixing.
    if universal and _POSTPROCESSING_MARKER in haystack:
        return CompatibilityConversionError(
            "The media downloaded, but it could not be converted for universal "
            f"playback: {message}",
            hint=(
                "The downloaded file was kept exactly as the source provided "
                "it, so it may not play in Apple or Windows players. Original "
                "quality keeps that same file without attempting conversion."
            ),
        )

    if any(marker in haystack for marker in _NO_FORMAT_MARKERS):
        return NoFormatMatchError(
            "No downloadable format matched the selected quality/options.",
            hint=(
                "This is about the requested quality, not about access to the "
                "media. Try Best quality, or the other compatibility mode."
            ),
        )

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
        postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._js_runtime = js_runtime
        self._factory: YDLFactory = ydl_factory or _default_factory
        self._progress_hook = progress_hook
        self._postprocessor_hook = postprocessor_hook
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

    def _extract(
        self,
        opts: dict[str, Any],
        url: str,
        *,
        download: bool,
        setup: Callable[[YoutubeDLLike], None] | None = None,
        universal: bool = False,
    ) -> dict[str, Any]:
        """Run yt-dlp and normalise both its errors and its return value.

        ``setup`` runs against the live session before extraction, which is
        where postprocessors have to be attached.
        """
        from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

        try:
            with self._session(opts) as ydl:
                if setup is not None:
                    setup(ydl)
                info = ydl.extract_info(url, download=download)
        except UnsupportedError as exc:
            raise DownloadFailedError(
                f"yt-dlp has no extractor for this URL: {url}",
                hint="Check the URL, or see the yt-dlp supported-sites list.",
            ) from exc
        except (DownloadError, ExtractorError) as exc:
            raise _classify_download_error(exc, universal=universal) from exc
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
            postprocessor_hooks=([self._postprocessor_hook] if self._postprocessor_hook else None),
            # post_hooks fire with the final path after every postprocessor and
            # file move, so this is the only reliable source for the result.
            post_hooks=[final_paths.append],
            quiet=not self._verbose,
        )

        # Only automatic names are cleaned: a user-supplied --filename template
        # is theirs, and is never rewritten beyond the safety validation.
        # Normalisation is separate and runs after the download, so both can
        # apply to the same job.
        def setup(ydl: YoutubeDLLike) -> None:
            if not request.filename_template:
                _register_auto_naming(ydl)
            if request.needs_universal_video:
                register_universal_compatibility(ydl)

        info = self._extract(
            opts,
            request.url,
            download=True,
            setup=setup,
            universal=request.needs_universal_video,
        )
        path = self._resolve_final_path(info, final_paths, request)
        outcome = self._describe_selection(info, request)
        if outcome.selection.startswith("fallback") or outcome.cap_exceeded:
            logger.info(
                "selection=%s audio=%s height=%s requested_quality=%s",
                outcome.selection,
                "available" if outcome.has_audio else "unavailable",
                outcome.height,
                outcome.requested_quality,
            )
        return DownloadResult(path=path, info=MediaInfo.from_info_dict(info), outcome=outcome)

    @staticmethod
    def _describe_selection(info: dict[str, Any], request: DownloadRequest) -> SelectionOutcome:
        """Read what was chosen from yt-dlp's own answer, not from a guess."""
        merged = info.get("requested_formats") or []
        if merged:
            has_audio = any(f.get("acodec") not in (None, "none") for f in merged)
            height = max((f.get("height") or 0) for f in merged) or None
            selection = "video_plus_audio" if has_audio else "fallback_video_only"
        else:
            has_audio = info.get("acodec") not in (None, "none")
            height = info.get("height")
            has_video = info.get("vcodec") not in (None, "none")
            if has_video and has_audio:
                selection = "muxed"
            elif has_video:
                selection = "fallback_video_only"
            else:
                selection = "audio_only"

        # Everything above is the extractor's account of what it *selected*.
        # Universal probes what it actually *produced*, and where that answer
        # exists it wins: a real download once reported video_plus_audio for a
        # file with no sound, which meant the "no audio" notice never reached
        # the person holding a silent video.
        probed = _probed_audio_codec(info)
        if probed is not _NOT_PROBED and bool(probed) is not bool(has_audio):
            logger.info(
                "selection corrected from the produced file: metadata_audio=%s file_audio=%s",
                "yes" if has_audio else "no",
                probed or "none",
            )
            has_audio = bool(probed)
            if not has_audio:
                selection = "fallback_video_only"
            elif selection == "fallback_video_only":
                selection = "video_plus_audio" if merged else "muxed"

        return SelectionOutcome(
            selection=selection,
            has_audio=bool(has_audio),
            height=height,
            requested_quality=request.quality,
        )

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
