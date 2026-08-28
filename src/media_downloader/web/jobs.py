"""Download jobs for the web UI.

A job is one download, run on a worker thread so the HTTP handler never blocks
for the length of a download. Progress arrives through yt-dlp's own hooks --
no terminal output is ever parsed -- and is stored as an immutable snapshot
that readers copy under a lock.

**Concurrency policy.** This version runs one download at a time and refuses a
second with :class:`DownloadInProgressError`. That policy lives only in
:meth:`JobManager.submit`; the surrounding shape (a ``QUEUED`` state, jobs
addressed by id, snapshots that already carry a position-independent status) is
deliberately queue-shaped, so adding a FIFO queue, cancellation or concurrent
workers later changes the manager without disturbing the downloader core, the
job record or the HTTP contract.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from media_downloader.config import DownloadRequest
from media_downloader.diagnostics import record_error
from media_downloader.downloader import DownloadResult
from media_downloader.errors import (
    DownloadFailedError,
    FFmpegRequiredError,
    InvalidURLError,
    MediaDownloaderError,
    MediaUnavailableError,
    OutputError,
)
from media_downloader.logging_setup import get_logger

logger = get_logger("web.jobs")

#: How many finished jobs stay visible in the session history. This is an
#: in-memory list, not a database: it is gone when the process exits.
HISTORY_LIMIT = 25

#: Postprocessors that are ours, not something the user should be told about.
#: AutoNamePP injects the cleaned filename at pre_process time.
IGNORED_POSTPROCESSORS = frozenset({"AutoName"})

#: Human-readable names for the phases that take real time. Compatibility
#: conversion can outlast the download itself, so leaving the interface sitting
#: at 100% with no explanation would look like a hang.
POSTPROCESSOR_STAGES: dict[str, str] = {
    "Merger": "Merging video and audio",
    "UniversalCompatibility": "Optimising compatibility",
    "ExtractAudio": "Extracting audio",
    "VideoConvertor": "Converting",
}
DEFAULT_STAGE = "Processing"


class JobState(str, Enum):
    """Lifecycle of a download job."""

    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {JobState.COMPLETED, JobState.FAILED}


class DownloadInProgressError(MediaDownloaderError):
    """Raised when a download is requested while another one is running."""


@dataclass(frozen=True)
class JobProgress:
    """An immutable snapshot of download progress.

    ``percent`` is ``None`` whenever the total size is unknown, so the UI can
    show an indeterminate bar instead of inventing a number.
    """

    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_seconds: int | None = None
    filename: str | None = None
    fragment_index: int | None = None
    fragment_count: int | None = None

    @property
    def percent(self) -> float | None:
        if not self.total_bytes or self.downloaded_bytes is None:
            return None
        return max(0.0, min(100.0, self.downloaded_bytes / self.total_bytes * 100))


@dataclass(frozen=True)
class JobError:
    """A failure, in the same words the CLI would have printed.

    ``error_id`` is present only for unexpected internal failures. It is the
    short code shown in the interface and written to the log, so a user can
    quote it and have the matching entry found.
    """

    code: str
    message: str
    hint: str | None = None
    error_id: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> JobError:
        if isinstance(exc, MediaDownloaderError):
            return cls(code=_error_code(exc), message=exc.message, hint=exc.hint)
        error_id = record_error(logger, exc, context="download job failed")
        return cls(
            code="UNEXPECTED_ERROR",
            message="Something went wrong while downloading.",
            hint=f"Error ID: {error_id}",
            error_id=error_id,
        )


#: Exception class -> stable machine-readable code. Written out rather than
#: derived from the class name so the wire contract is deliberate and does not
#: change if a class is ever renamed, and so acronyms stay readable.
_ERROR_CODES: dict[type[MediaDownloaderError], str] = {
    InvalidURLError: "INVALID_URL",
    FFmpegRequiredError: "FFMPEG_REQUIRED",
    DownloadFailedError: "DOWNLOAD_FAILED",
    MediaUnavailableError: "MEDIA_UNAVAILABLE",
    OutputError: "OUTPUT_ERROR",
    DownloadInProgressError: "DOWNLOAD_IN_PROGRESS",
}


def register_error_code(exc_type: type[MediaDownloaderError], code: str) -> None:
    """Register a wire code for an exception defined outside this module.

    Keeps the mapping explicit and in one place while letting other subsystems
    contribute their own codes without importing them here.
    """
    _ERROR_CODES[exc_type] = code


def _log_state(job: Job, state: JobState, **fields: object) -> None:
    """Record one lifecycle transition.

    Deliberately narrow. Support needs to answer "did it finish, and which
    file?", which takes a job id, the service, a media id and the final
    filename -- not the URL. The source URL is never logged to obtain them,
    and neither are cookies, tokens, credentials, headers, query strings,
    fragments or arbitrary extractor metadata.
    """
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("job %s state=%s%s", job.id, state.value, f" {extra}" if extra else "")


def _error_code(exc: MediaDownloaderError) -> str:
    """Map an exception to its wire code, most specific class first."""
    for klass in type(exc).__mro__:
        code = _ERROR_CODES.get(klass)
        if code is not None:
            return code
    return "UNEXPECTED_ERROR"


@dataclass
class Job:
    """One download, mutated only by its worker under ``JobManager._lock``."""

    id: str
    request: DownloadRequest
    state: JobState = JobState.QUEUED
    progress: JobProgress = field(default_factory=JobProgress)
    title: str | None = None
    #: What the processing phase is currently doing, in plain words. None until
    #: a postprocessor that takes real time starts.
    stage: str | None = None
    #: Plain sentences about anything surprising in the result -- a file with
    #: no sound, or a quality cap that could not be honoured. Empty is the
    #: ordinary case.
    notes: tuple[str, ...] = ()
    result_path: Path | None = None
    error: JobError | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        """Serialise for the API. Only the file *name* is exposed, plus the
        full path once the download has finished, so nothing leaks about the
        filesystem before there is a real result to report."""
        return {
            "id": self.id,
            "state": self.state.value,
            "stage": self.stage,
            "notes": list(self.notes),
            "url": self.request.url,
            "title": self.title,
            "audio_only": self.request.audio_only,
            "progress": {
                "percent": self.progress.percent,
                "downloaded_bytes": self.progress.downloaded_bytes,
                "total_bytes": self.progress.total_bytes,
                "speed_bps": self.progress.speed_bps,
                "eta_seconds": self.progress.eta_seconds,
                "filename": self.progress.filename,
                "fragment_index": self.progress.fragment_index,
                "fragment_count": self.progress.fragment_count,
            },
            "result": (
                {"filename": self.result_path.name, "path": str(self.result_path)}
                if self.result_path is not None
                else None
            ),
            "error": (
                {
                    "code": self.error.code,
                    "message": self.error.message,
                    "hint": self.error.hint,
                    "error_id": self.error.error_id,
                }
                if self.error is not None
                else None
            ),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


#: Builds the downloader for a job. Injected so tests never touch the network.
DownloaderFactory = Callable[..., Any]


class JobManager:
    """Owns the job history and the single worker slot."""

    def __init__(
        self,
        downloader_factory: DownloaderFactory,
        *,
        history_limit: int = HISTORY_LIMIT,
    ) -> None:
        self._downloader_factory = downloader_factory
        self._history_limit = history_limit
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._active_id: str | None = None
        self._threads: list[threading.Thread] = []

    # -- queries ---------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def history(self) -> list[Job]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._jobs.values()))

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._active_id is not None

    # -- submission ------------------------------------------------------

    def submit(self, request: DownloadRequest) -> Job:
        """Accept a download and start it on a worker thread.

        Raises:
            DownloadInProgressError: if a download is already running. This is
                the only place the one-at-a-time policy is expressed.
        """
        job = Job(id=secrets.token_urlsafe(8), request=request)

        with self._lock:
            if self._active_id is not None:
                raise DownloadInProgressError(
                    "A download is already in progress.",
                    hint="Wait for the current download to finish, then try again.",
                )
            self._active_id = job.id
            self._jobs[job.id] = job
            self._prune_locked()

        thread = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"dl-{job.id}")
        self._threads.append(thread)
        thread.start()
        return job

    def _prune_locked(self) -> None:
        """Drop the oldest finished jobs beyond the history limit."""
        while len(self._jobs) > self._history_limit:
            for job_id, job in self._jobs.items():
                if job.state.is_terminal:
                    del self._jobs[job_id]
                    break
            else:  # pragma: no cover - only reachable with limit active jobs
                return

    # -- worker ----------------------------------------------------------

    def _run(self, job: Job) -> None:
        """Execute one download. Never raises; failures land on the job."""
        try:
            self._set_state(job, JobState.PREPARING)
            downloader = self._downloader_factory(
                progress_hook=self._make_progress_hook(job),
                postprocessor_hook=self._make_postprocessor_hook(job),
            )
            result = downloader.download(job.request)
            self._complete(job, result)
        except BaseException as exc:  # a worker thread must never let anything escape
            logger.debug("Job %s failed: %s", job.id, exc)
            self._fail(job, JobError.from_exception(exc))
        finally:
            # Backstop only. Both terminal paths release the slot themselves,
            # atomically with the state change -- see _release_locked.
            with self._lock:
                self._release_locked(job)

    # -- state transitions (all serialised on the lock) -------------------

    def _release_locked(self, job: Job) -> None:
        """Give up the one-at-a-time slot. Caller must hold the lock.

        Called while setting a terminal state so that "finished" and "not busy"
        become visible in the same instant. Releasing afterwards left a window
        where a job already reported COMPLETED but a fresh submit still raised
        DownloadInProgressError -- exactly what a user hitting Download again
        the moment the UI says done would see.
        """
        if self._active_id == job.id:
            self._active_id = None

    def _set_state(self, job: Job, state: JobState) -> None:
        with self._lock:
            changed = job.state is not state
            job.state = state
        if changed:
            _log_state(job, state)

    def _complete(self, job: Job, result: DownloadResult) -> None:
        with self._lock:
            job.state = JobState.COMPLETED
            job.result_path = result.path
            job.title = result.info.title
            job.finished_at = time.time()
            # A finished download is 100% by definition, even when the size
            # was never known up front.
            if job.progress.total_bytes:
                job.progress = replace(job.progress, downloaded_bytes=job.progress.total_bytes)
            job.progress = replace(job.progress, filename=result.path.name)
            if result.outcome is not None:
                job.notes = tuple(result.outcome.notices())
            # A finished job is not in a stage any more.
            job.stage = None
            self._release_locked(job)
        _log_state(
            job,
            JobState.COMPLETED,
            service=result.info.extractor,
            media_id=result.info.media_id,
            filename=result.path.name,
            quality=job.request.quality,
            compatibility=job.request.compatibility.value,
            selection=result.outcome.selection if result.outcome else None,
        )

    def _fail(self, job: Job, error: JobError) -> None:
        with self._lock:
            job.state = JobState.FAILED
            job.error = error
            job.finished_at = time.time()
            job.stage = None
            self._release_locked(job)
        # Correlates with the error ID the interface shows the user.
        _log_state(job, JobState.FAILED, code=error.code, error_id=error.error_id)

    # -- yt-dlp hooks ----------------------------------------------------

    def _make_progress_hook(self, job: Job) -> Callable[[dict[str, Any]], None]:
        """Build the yt-dlp progress hook for ``job``.

        Runs on the worker thread and must never raise: an exception here would
        abort an otherwise healthy download.
        """

        def hook(status: dict[str, Any]) -> None:
            try:
                if status.get("status") != "downloading":
                    return
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                info = status.get("info_dict") or {}
                with self._lock:
                    if job.state.is_terminal:
                        return
                    entering = job.state is not JobState.DOWNLOADING
                    job.state = JobState.DOWNLOADING
                    if job.title is None and info.get("title"):
                        job.title = str(info["title"])
                    job.progress = JobProgress(
                        downloaded_bytes=status.get("downloaded_bytes"),
                        total_bytes=int(total) if total else None,
                        speed_bps=status.get("speed"),
                        eta_seconds=status.get("eta"),
                        filename=Path(str(status["filename"])).name
                        if status.get("filename")
                        else job.progress.filename,
                        fragment_index=status.get("fragment_index"),
                        fragment_count=status.get("fragment_count"),
                    )
                if entering:
                    _log_state(job, JobState.DOWNLOADING)
            except Exception:  # pragma: no cover - reporting must not break a download
                logger.debug("Progress hook failed for job %s", job.id)

        return hook

    def _make_postprocessor_hook(self, job: Job) -> Callable[[dict[str, Any]], None]:
        """Build the yt-dlp postprocessor hook: the FFmpeg merge / convert phase.

        Two filters keep this honest. Our own naming postprocessor runs at
        ``pre_process``, before any bytes move, so reporting it would flash
        "Processing" at the user before the download even starts; and any other
        pre-download postprocessor would do the same. So we ignore our own by
        name, and only accept the phase once downloading has actually begun.
        """

        def hook(status: dict[str, Any]) -> None:
            try:
                if status.get("status") not in {"started", "processing"}:
                    return
                if str(status.get("postprocessor") or "") in IGNORED_POSTPROCESSORS:
                    return
                name = str(status.get("postprocessor") or "")
                stage = POSTPROCESSOR_STAGES.get(name)
                entering = False
                with self._lock:
                    # Only a postprocessor that runs after the download is a
                    # phase worth showing.
                    if job.state is JobState.DOWNLOADING:
                        job.state = JobState.PROCESSING
                        entering = True
                    if job.state is JobState.PROCESSING:
                        # No invented percentage: FFmpeg progress is not
                        # reliably derivable here, so the interface says what is
                        # happening and shows an indeterminate indicator.
                        # A step we have no name for must not overwrite one we
                        # do: yt-dlp runs its own fixups after ours, and letting
                        # them replace "Optimising compatibility" with a generic
                        # word would lose the only useful thing on screen.
                        job.stage = stage or job.stage or DEFAULT_STAGE
                if entering:
                    _log_state(job, JobState.PROCESSING)
            except Exception:  # pragma: no cover
                logger.debug("Postprocessor hook failed for job %s", job.id)

        return hook

    # -- shutdown --------------------------------------------------------

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Join worker threads. Returns True if all finished within ``timeout``."""
        for thread in list(self._threads):
            thread.join(timeout)
        return not any(t.is_alive() for t in self._threads)
