"""Terminal progress reporting driven by yt-dlp's progress hooks.

An interactive terminal gets a Rich progress bar with speed and ETA. When
stdout is redirected -- a pipe, a log file, CI -- the bar would only produce
noise, so throttled plain-text lines are emitted instead.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

PLAIN_UPDATE_INTERVAL_SECONDS = 2.0


def _describe(status: dict[str, Any]) -> str:
    """Short label for the fragment currently being downloaded."""
    info = status.get("info_dict") or {}
    if info.get("vcodec") == "none":
        return "Audio"
    if info.get("acodec") == "none":
        return "Video"
    return "Media"


class ProgressReporter:
    """Renders yt-dlp download progress.

    Used as a context manager so the Rich live display is always torn down,
    including when a download raises or the user interrupts it.
    """

    def __init__(self, console: Console, *, enabled: bool = True) -> None:
        self._console = console
        self._enabled = enabled
        self._rich = enabled and console.is_terminal
        self._progress: Progress | None = None
        self._task: TaskID | None = None
        self._current_key: str | None = None
        self._last_plain_update = 0.0

    def __enter__(self) -> ProgressReporter:
        if self._rich:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=self._console,
                transient=True,
            )
            self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task = None

    # -- yt-dlp hook ----------------------------------------------------

    def hook(self, status: dict[str, Any]) -> None:
        """Progress hook passed to yt-dlp.

        Never raises: a failure to draw a progress bar must not abort an
        otherwise healthy download.
        """
        if not self._enabled:
            return
        try:
            state = status.get("status")
            if state == "downloading":
                self._on_downloading(status)
            elif state == "finished":
                self._on_finished(status)
        except Exception:  # pragma: no cover - display must never break a download
            pass

    def _on_downloading(self, status: dict[str, Any]) -> None:
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        completed = status.get("downloaded_bytes") or 0
        key = str(status.get("filename") or _describe(status))

        if self._progress is not None:
            if self._task is None or key != self._current_key:
                if self._task is not None:
                    self._progress.remove_task(self._task)
                self._task = self._progress.add_task(_describe(status), total=total)
                self._current_key = key
            self._progress.update(self._task, completed=completed, total=total)
            return

        now = time.monotonic()
        if now - self._last_plain_update < PLAIN_UPDATE_INTERVAL_SECONDS:
            return
        self._last_plain_update = now
        if total:
            pct = completed / total * 100
            self._console.print(f"{_describe(status)}: {pct:5.1f}%  ({completed}/{total} bytes)")
        else:
            self._console.print(f"{_describe(status)}: {completed} bytes")

    def _on_finished(self, status: dict[str, Any]) -> None:
        if self._progress is not None and self._task is not None:
            total = status.get("total_bytes") or status.get("downloaded_bytes")
            if total:
                self._progress.update(self._task, completed=total, total=total)
            return
        self._last_plain_update = 0.0
        self._console.print(f"{_describe(status)}: download complete, processing...")
