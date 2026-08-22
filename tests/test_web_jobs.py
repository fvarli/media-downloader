"""Job lifecycle, progress translation and the one-at-a-time policy.

Every downloader here is a fake, so nothing touches the network.
"""

from __future__ import annotations

import threading
import time
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
