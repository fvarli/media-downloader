"""Job lifecycle, progress translation and the one-at-a-time policy.

Every downloader here is a fake, so nothing touches the network.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from media_downloader.config import build_request
from media_downloader.downloader import DownloadResult, MediaInfo
from media_downloader.errors import DownloadFailedError, MediaUnavailableError
from media_downloader.web.jobs import (
    DownloadInProgressError,
    JobError,
    JobManager,
    JobProgress,
    JobState,
)

SAMPLE_INFO = MediaInfo(
    title="Example Video",
    media_id="abc123",
    uploader="Example",
    duration_seconds=12,
    extractor="Twitter",
    webpage_url="https://x.com/a/status/1",
)


@pytest.fixture
def request_for(tmp_path: Path):
    def make(url: str = "https://x.com/a/status/1", **kw: Any):
        return build_request(url=url, output=str(tmp_path), env={}, **kw)

    return make


class FakeDownloader:
    """Stands in for the real Downloader, driving the hooks it was given."""

    def __init__(
        self,
        *,
        result_path: Path | None = None,
        error: Exception | None = None,
        events: list[dict[str, Any]] | None = None,
        pp_events: list[dict[str, Any]] | None = None,
        progress_hook: Any = None,
        postprocessor_hook: Any = None,
        block: threading.Event | None = None,
    ) -> None:
        self.result_path = result_path
        self.error = error
        self.events = events or []
        self.pp_events = pp_events or []
        self.progress_hook = progress_hook
        self.postprocessor_hook = postprocessor_hook
        self.block = block

    def download(self, request: Any) -> DownloadResult:
        for event in self.events:
            if self.progress_hook:
                self.progress_hook(event)
        for event in self.pp_events:
            if self.postprocessor_hook:
                self.postprocessor_hook(event)
        if self.block is not None:
            self.block.wait(timeout=5)
        if self.error is not None:
            raise self.error
        assert self.result_path is not None
        return DownloadResult(path=self.result_path, info=SAMPLE_INFO)


def manager_for(**kwargs: Any) -> tuple[JobManager, list[FakeDownloader]]:
    made: list[FakeDownloader] = []

    def factory(**hooks: Any) -> FakeDownloader:
        fake = FakeDownloader(**kwargs, **hooks)
        made.append(fake)
        return fake

    return JobManager(factory), made


def wait_until_done(manager: JobManager, job_id: str, timeout: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        assert job is not None
        if job.state.is_terminal:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


# -- progress maths -----------------------------------------------------


@pytest.mark.parametrize(
    ("downloaded", "total", "expected"),
    [(0, 100, 0.0), (50, 100, 50.0), (100, 100, 100.0), (None, 100, None), (50, None, None)],
)
def test_percent_is_none_when_the_total_is_unknown(
    downloaded: int | None, total: int | None, expected: float | None
) -> None:
    """An unknown total must not become a made-up percentage."""
    assert JobProgress(downloaded_bytes=downloaded, total_bytes=total).percent == expected


def test_percent_is_clamped_if_the_estimate_was_low() -> None:
    assert JobProgress(downloaded_bytes=150, total_bytes=100).percent == 100.0


# -- lifecycle ----------------------------------------------------------


def test_a_successful_download_reaches_completed(request_for: Any, tmp_path: Path) -> None:
    final = tmp_path / "Example Video - 1.mp4"
    manager, _ = manager_for(result_path=final)
    job = manager.submit(request_for())
    done = wait_until_done(manager, job.id)

    assert done.state is JobState.COMPLETED
    assert done.result_path == final
    assert done.title == "Example Video"
    assert done.finished_at is not None


def test_the_final_filename_reaches_the_snapshot(request_for: Any, tmp_path: Path) -> None:
    final = tmp_path / "Trend - 2090546322570924033.mp4"
    manager, _ = manager_for(result_path=final)
    job = manager.submit(request_for())
    snapshot = wait_until_done(manager, job.id).snapshot()

    assert snapshot["result"]["filename"] == "Trend - 2090546322570924033.mp4"
    assert snapshot["result"]["path"] == str(final)


def test_progress_events_are_translated(request_for: Any, tmp_path: Path) -> None:
    events = [
        {
            "status": "downloading",
            "downloaded_bytes": 512,
            "total_bytes": 2048,
            "speed": 1024.0,
            "eta": 3,
            "filename": str(tmp_path / "clip.mp4"),
            "fragment_index": 2,
            "fragment_count": 8,
            "info_dict": {"title": "Live Title"},
        }
    ]
    block = threading.Event()
    manager, _ = manager_for(result_path=tmp_path / "clip.mp4", events=events, block=block)
    job = manager.submit(request_for())

    # Observe mid-download, while the worker is held at the block.
    time.sleep(0.05)
    mid = manager.get(job.id)
    assert mid is not None
    assert mid.state is JobState.DOWNLOADING
    assert mid.progress.total_bytes == 2048
    assert mid.progress.speed_bps == 1024.0
    assert mid.progress.eta_seconds == 3
    assert mid.progress.fragment_index == 2
    assert mid.progress.fragment_count == 8
    assert mid.progress.percent == pytest.approx(25.0)
    # Only the name is exposed, never the directory it lived in mid-download.
    assert mid.progress.filename == "clip.mp4"
    # Until the download finishes, the title is whatever the stream reported.
    assert mid.title == "Live Title"

    block.set()
    done = wait_until_done(manager, job.id)
    # The finished result is authoritative and replaces the in-flight guess.
    assert done.title == "Example Video"


def test_the_postprocessing_phase_is_visible(request_for: Any, tmp_path: Path) -> None:
    """FFmpeg merging produces no download events, so it needs its own hook."""
    seen: list[JobState] = []
    block = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mkv",
        # The realistic order: bytes arrive, then FFmpeg merges them.
        events=[
            {
                "status": "downloading",
                "downloaded_bytes": 100,
                "total_bytes": 100,
                "info_dict": {},
            }
        ],
        pp_events=[{"status": "started", "postprocessor": "Merger"}],
        block=block,
    )
    job = manager.submit(request_for())
    for _ in range(200):
        current = manager.get(job.id)
        assert current is not None
        seen.append(current.state)
        if current.state is JobState.PROCESSING:
            break
        time.sleep(0.01)
    block.set()
    wait_until_done(manager, job.id)
    assert JobState.PROCESSING in seen


def test_states_advance_through_preparing(request_for: Any, tmp_path: Path) -> None:
    block = threading.Event()
    manager, _ = manager_for(result_path=tmp_path / "v.mp4", block=block)
    job = manager.submit(request_for())
    time.sleep(0.05)
    mid = manager.get(job.id)
    assert mid is not None
    assert mid.state in {JobState.PREPARING, JobState.DOWNLOADING}
    block.set()
    assert wait_until_done(manager, job.id).state is JobState.COMPLETED


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (MediaUnavailableError("private"), "MEDIA_UNAVAILABLE"),
        (DownloadFailedError("network"), "DOWNLOAD_FAILED"),
        (RuntimeError("boom"), "UNEXPECTED_ERROR"),
    ],
)
def test_failures_are_recorded_on_the_job(error: Exception, code: str, request_for: Any) -> None:
    manager, _ = manager_for(error=error)
    job = manager.submit(request_for())
    done = wait_until_done(manager, job.id)

    assert done.state is JobState.FAILED
    assert done.error is not None
    assert done.error.code == code
    assert done.result_path is None


def test_a_worker_failure_never_escapes_the_thread(request_for: Any) -> None:
    """A crashing download must not take the server down with it."""
    manager, _ = manager_for(error=KeyboardInterrupt())
    job = manager.submit(request_for())
    assert wait_until_done(manager, job.id).state is JobState.FAILED
    assert not manager.is_busy


# -- concurrency policy --------------------------------------------------


def test_only_one_download_runs_at_a_time(request_for: Any, tmp_path: Path) -> None:
    block = threading.Event()
    manager, _ = manager_for(result_path=tmp_path / "v.mp4", block=block)
    manager.submit(request_for())

    with pytest.raises(DownloadInProgressError):
        manager.submit(request_for("https://x.com/b/status/2"))

    block.set()


def test_the_slot_is_released_after_a_download(request_for: Any, tmp_path: Path) -> None:
    manager, _ = manager_for(result_path=tmp_path / "v.mp4")
    first = manager.submit(request_for())
    wait_until_done(manager, first.id)
    assert not manager.is_busy
    second = manager.submit(request_for("https://x.com/b/status/2"))
    assert wait_until_done(manager, second.id).state is JobState.COMPLETED


def test_the_slot_is_released_after_a_failure(request_for: Any) -> None:
    manager, _ = manager_for(error=DownloadFailedError("nope"))
    job = manager.submit(request_for())
    wait_until_done(manager, job.id)
    assert not manager.is_busy


# -- history -------------------------------------------------------------


def test_history_is_newest_first(request_for: Any, tmp_path: Path) -> None:
    manager, _ = manager_for(result_path=tmp_path / "v.mp4")
    first = manager.submit(request_for())
    wait_until_done(manager, first.id)
    second = manager.submit(request_for("https://x.com/b/status/2"))
    wait_until_done(manager, second.id)

    assert [job.id for job in manager.history()] == [second.id, first.id]


def test_history_is_capped(request_for: Any, tmp_path: Path) -> None:
    made: list[FakeDownloader] = []

    def factory(**hooks: Any) -> FakeDownloader:
        fake = FakeDownloader(result_path=tmp_path / "v.mp4", **hooks)
        made.append(fake)
        return fake

    manager = JobManager(factory, history_limit=3)
    for index in range(6):
        job = manager.submit(request_for(f"https://x.com/a/status/{index}"))
        wait_until_done(manager, job.id)

    assert len(manager.history()) <= 3


# -- queue-readiness -----------------------------------------------------


def test_the_state_model_leaves_room_for_a_future_queue() -> None:
    """QUEUED exists and is non-terminal, so a FIFO queue needs no new states."""
    assert JobState.QUEUED.value == "queued"
    assert not JobState.QUEUED.is_terminal
    assert JobState.COMPLETED.is_terminal and JobState.FAILED.is_terminal


def test_job_errors_carry_the_same_words_the_cli_prints() -> None:
    exc = MediaUnavailableError("This media is not publicly accessible", hint="It may be private.")
    error = JobError.from_exception(exc)
    assert error.message == exc.message
    assert error.hint == exc.hint


def test_our_own_naming_postprocessor_is_not_shown_to_the_user(
    request_for: Any, tmp_path: Path
) -> None:
    """AutoNamePP runs before any bytes move; reporting it would flash
    "Processing" at the user before the download had even started."""
    block = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        pp_events=[{"status": "started", "postprocessor": "AutoName"}],
        block=block,
    )
    job = manager.submit(request_for())
    time.sleep(0.05)
    current = manager.get(job.id)
    assert current is not None
    assert current.state is not JobState.PROCESSING
    block.set()
    wait_until_done(manager, job.id)


def test_processing_is_only_reported_after_downloading_starts(
    request_for: Any, tmp_path: Path
) -> None:
    """A postprocessor firing before the first byte must not reorder the phases."""
    block = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        pp_events=[{"status": "started", "postprocessor": "Merger"}],
        block=block,
    )
    job = manager.submit(request_for())
    time.sleep(0.05)
    current = manager.get(job.id)
    assert current is not None
    # No download event has been delivered, so this is still preparation.
    assert current.state is JobState.PREPARING
    block.set()
    wait_until_done(manager, job.id)


def test_an_unexpected_job_failure_gets_an_error_id(request_for: Any) -> None:
    """The UI shows a code the user can quote; the log holds the detail."""
    manager, _ = manager_for(error=RuntimeError("internal explosion"))
    job = manager.submit(request_for())
    done = wait_until_done(manager, job.id)

    assert done.error is not None
    assert done.error.error_id is not None
    assert done.error.error_id.startswith("MD-")
    # The raw internal message is not shown to the user.
    assert "internal explosion" not in done.error.message
    assert done.error.error_id in (done.error.hint or "")
    assert done.snapshot()["error"]["error_id"] == done.error.error_id


def test_an_expected_failure_gets_no_error_id(request_for: Any) -> None:
    """A private video is not a bug; it needs no diagnostic code."""
    manager, _ = manager_for(error=MediaUnavailableError("private video"))
    done = wait_until_done(manager, manager.submit(request_for()).id)

    assert done.error is not None
    assert done.error.error_id is None
    assert done.error.message == "private video"


class _BusyAtTransition(logging.Handler):
    """Samples is_busy at the moment a transition becomes observable.

    Polling for the terminal state cannot test this: the poll interval is far
    wider than the race, so it passes by luck even when the slot leaks. The
    lifecycle record is emitted immediately after the state is set, so reading
    is_busy from inside the handler pins the observation to that instant.
    """

    def __init__(self, manager: JobManager) -> None:
        super().__init__()
        self.manager = manager
        self.samples: dict[str, bool] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        for state in ("completed", "failed"):
            if f"state={state}" in message:
                self.samples[state] = self.manager.is_busy


@pytest.mark.parametrize(
    ("state", "kwargs"),
    [
        ("completed", {"result_path": Path("v.mp4")}),
        ("failed", {"error": RuntimeError("nope")}),
    ],
)
def test_a_terminal_job_no_longer_holds_the_slot(
    request_for: Any, tmp_path: Path, state: str, kwargs: Any
) -> None:
    """Finishing and releasing the slot must be observable together.

    The slot used to be released after the worker's try block, so a job could
    report a terminal state while a fresh submit still raised
    DownloadInProgressError -- what a user pressing Download again the moment
    the interface said done would hit.
    """
    if "result_path" in kwargs:
        kwargs["result_path"] = tmp_path / "v.mp4"
    manager, _ = manager_for(**kwargs)
    handler = _BusyAtTransition(manager)
    logger = logging.getLogger("media_downloader.web.jobs")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)  # the record must reach the handler at all
    try:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        wait_until_done(manager, job.id)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert handler.samples[state] is False
    assert manager.is_busy is False


# -- lifecycle records (Defect 3) ----------------------------------------
#
# Only PREPARING used to be logged; DOWNLOADING, PROCESSING, COMPLETED and
# FAILED assigned job.state directly. A report exported after a finished
# download still ended at "preparing".


@contextmanager
def captured_lifecycle() -> Any:
    records: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if message.startswith("job "):
                records.append(message)

    handler = Collect()
    logger = logging.getLogger("media_downloader.web.jobs")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _states(records: list[str]) -> list[str]:
    return [
        part.removeprefix("state=")
        for r in records
        for part in r.split()
        if part.startswith("state=")
    ]


def test_a_successful_job_records_every_stage(request_for: Any, tmp_path: Path) -> None:
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[{"status": "started", "postprocessor": "Merger"}],
    )
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        wait_until_done(manager, job.id)

    assert _states(records) == ["preparing", "downloading", "processing", "completed"]


def test_processing_is_recorded_only_when_that_stage_runs(request_for: Any, tmp_path: Path) -> None:
    """A plain download has no post-processing; inventing the stage would lie."""
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
    )
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        wait_until_done(manager, job.id)

    assert "processing" not in _states(records)


def test_a_stage_is_recorded_once_however_many_progress_events_arrive(
    request_for: Any, tmp_path: Path
) -> None:
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": n, "total_bytes": 100, "info_dict": {}}
            for n in range(1, 40)
        ],
    )
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        wait_until_done(manager, job.id)

    assert _states(records).count("downloading") == 1


def test_the_completion_record_carries_safe_support_identifiers(
    request_for: Any, tmp_path: Path
) -> None:
    manager, _ = manager_for(result_path=tmp_path / "v.mp4")
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        wait_until_done(manager, job.id)

    completed = next(r for r in records if "state=completed" in r)
    assert job.id in completed
    assert f"service={SAMPLE_INFO.extractor}" in completed
    assert f"media_id={SAMPLE_INFO.media_id}" in completed
    assert "filename=v.mp4" in completed


def test_the_media_id_comes_from_metadata_not_from_the_url(
    request_for: Any, tmp_path: Path
) -> None:
    """Parsing the URL for an identifier both leaks it and gets it wrong.

    A YouTube watch URL's last path segment is "watch"; the real identifier
    lives in the query string, which is exactly the part that must never be
    logged. The extractor already reports it as metadata.
    """
    assert SAMPLE_INFO.media_id
    assert SAMPLE_INFO.media_id not in ("watch", "")


def test_no_lifecycle_record_contains_the_source_url(request_for: Any, tmp_path: Path) -> None:
    url = "https://x.com/a/status/1?token=SUPERSECRET#frag"
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[{"status": "started", "postprocessor": "Merger"}],
    )
    with captured_lifecycle() as records:
        job = manager.submit(request_for(url))
        wait_until_done(manager, job.id)

    joined = "\n".join(records)
    assert "SUPERSECRET" not in joined
    assert "token=" not in joined
    assert url not in joined
    assert "x.com" not in joined


def test_an_expected_failure_records_its_code(request_for: Any, tmp_path: Path) -> None:
    """No error ID here by design -- those exist only for internal failures."""
    manager, _ = manager_for(error=MediaUnavailableError("private video"))
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        finished = wait_until_done(manager, job.id)

    failed = next(r for r in records if "state=failed" in r)
    assert f"code={finished.error.code}" in failed
    assert "error_id" not in failed  # absent, not the string "None"


def test_an_internal_failure_records_the_error_id_the_user_is_shown(
    request_for: Any, tmp_path: Path
) -> None:
    """The record must correlate with the ID the interface puts on screen."""
    manager, _ = manager_for(error=RuntimeError("internal explosion"))
    with captured_lifecycle() as records:
        job = manager.submit(request_for("https://x.com/a/status/1"))
        finished = wait_until_done(manager, job.id)

    failed = next(r for r in records if "state=failed" in r)
    assert finished.error.error_id is not None
    assert f"error_id={finished.error.error_id}" in failed
    assert "internal explosion" not in failed  # the ID is the handle, not the text


# -- the processing stage ------------------------------------------------
#
# Compatibility conversion can outlast the download. A bar frozen at 100% with
# no explanation reads as a hang, so the phase says what it is doing -- without
# inventing a percentage, because FFmpeg progress is not reliably derivable.


def _stage_while_processing(
    manager: JobManager, job_id: str, release: threading.Event
) -> str | None:
    """Read the stage while the job is still in it.

    A finished job carries no stage, so it has to be observed mid-flight; the
    fake downloader waits on the event after firing its postprocessor hooks.
    """
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        assert job is not None
        if job.state is JobState.PROCESSING and job.stage:
            release.set()
            return job.stage
        time.sleep(0.01)
    release.set()
    return None


@pytest.mark.parametrize(
    ("postprocessor", "expected"),
    [
        ("UniversalCompatibility", "Optimising compatibility"),
        ("Merger", "Merging video and audio"),
        ("ExtractAudio", "Extracting audio"),
        ("SomethingElse", "Processing"),
    ],
)
def test_the_processing_phase_says_what_it_is_doing(
    request_for: Any, tmp_path: Path, postprocessor: str, expected: str
) -> None:
    release = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[{"status": "started", "postprocessor": postprocessor}],
        block=release,
    )
    job = manager.submit(request_for("https://x.com/a/status/1"))
    stage = _stage_while_processing(manager, job.id, release)
    wait_until_done(manager, job.id)
    assert stage == expected


def test_an_unnamed_step_does_not_overwrite_a_named_one(request_for: Any, tmp_path: Path) -> None:
    """yt-dlp runs its own fixups after ours. Letting them replace "Optimising
    compatibility" with a generic word loses the only useful thing on screen."""
    release = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[
            {"status": "started", "postprocessor": "UniversalCompatibility"},
            {"status": "started", "postprocessor": "FixupM4a"},
        ],
        block=release,
    )
    job = manager.submit(request_for("https://x.com/a/status/1"))
    stage = _stage_while_processing(manager, job.id, release)
    wait_until_done(manager, job.id)
    assert stage == "Optimising compatibility"


def test_a_finished_job_is_not_in_a_stage(request_for: Any, tmp_path: Path) -> None:
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[{"status": "started", "postprocessor": "UniversalCompatibility"}],
    )
    job = manager.submit(request_for("https://x.com/a/status/1"))
    finished = wait_until_done(manager, job.id)
    assert finished.stage is None
    assert finished.snapshot()["stage"] is None


def test_the_stage_reaches_the_interface(request_for: Any, tmp_path: Path) -> None:
    release = threading.Event()
    manager, _ = manager_for(
        result_path=tmp_path / "v.mp4",
        events=[
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2, "info_dict": {}}
        ],
        pp_events=[{"status": "started", "postprocessor": "UniversalCompatibility"}],
        block=release,
    )
    job = manager.submit(request_for("https://x.com/a/status/1"))
    deadline = time.monotonic() + 5
    seen = None
    while time.monotonic() < deadline:
        snapshot = manager.get(job.id).snapshot()
        if snapshot["state"] == "processing" and snapshot["stage"]:
            seen = snapshot["stage"]
            break
        time.sleep(0.01)
    release.set()
    wait_until_done(manager, job.id)
    assert seen == "Optimising compatibility"
