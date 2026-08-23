"""Web-facing wrapper around the managed-tools subsystem.

Installing a tool takes minutes and downloads tens of megabytes, so it runs on
a worker thread and the page polls for the result -- the same shape the
download jobs already use.

The browser never names a URL, a version or a path. It can only ask, by fixed
tool name, for the pinned thing the manifest already describes.
"""

from __future__ import annotations

import threading
from typing import Any

from media_downloader.diagnostics import record_error
from media_downloader.errors import MediaDownloaderError
from media_downloader.ffmpeg import FFmpegStatus, detect_ffmpeg
from media_downloader.jsruntime import detect_js_runtime
from media_downloader.logging_setup import get_logger
from media_downloader.tools.manager import (
    ToolInstallError,
    ToolManager,
    ToolState,
    ToolStatus,
)
from media_downloader.tools.manifest import DENO, FFMPEG
from media_downloader.web.jobs import register_error_code

logger = get_logger("web.tools")

# A failed or refused install is a client-visible precondition, not a crash.
register_error_code(ToolInstallError, "TOOL_INSTALL")

#: Why each tool exists, in the words the user sees before consenting.
TOOL_PURPOSE: dict[str, str] = {
    FFMPEG: (
        "FFmpeg merges the separate video and audio streams that sites use for "
        "their highest quality, and converts audio to formats like MP3. Without "
        "it, downloads still work but quality may be lower."
    ),
    DENO: (
        "A JavaScript runtime lets YouTube's challenges be solved, which some "
        "YouTube downloads need. Other sites do not use it."
    ),
}


class ToolInstaller:
    """Tracks install state for the web UI."""

    def __init__(self, manager: ToolManager | None = None) -> None:
        self._manager = manager or ToolManager()
        self._lock = threading.Lock()
        self._installing: set[str] = set()
        self._errors: dict[str, str] = {}
        self._threads: list[threading.Thread] = []

    @property
    def known_tools(self) -> tuple[str, ...]:
        return (FFMPEG, DENO)

    # -- queries ---------------------------------------------------------

    def _system_path(self, tool: str) -> Any:
        """Where the existing detection found this tool, if it did.

        Reuses ffmpeg.py and jsruntime.py rather than re-implementing PATH
        lookup, so system tools keep winning exactly as they did before.
        """
        if tool == FFMPEG:
            status: FFmpegStatus = detect_ffmpeg()
            # Only a *system* copy counts here; a managed one is reported by
            # the manager itself, which knows its own directory.
            managed = self._manager.managed_dir(FFMPEG)
            if status.available and (managed is None or status.location != managed):
                return status.ffmpeg
            return None

        runtime = detect_js_runtime()
        if runtime.available and not runtime.managed:
            return runtime.path
        return None

    def status(self, tool: str) -> ToolStatus:
        with self._lock:
            installing = tool in self._installing
            error = self._errors.get(tool)

        if installing:
            base = self._manager.status(tool, system_path=None)
            return ToolStatus(
                tool=tool,
                state=ToolState.INSTALLING,
                version=base.version,
                size_bytes=base.size_bytes,
                licence=base.licence,
                source=base.source,
            )

        status = self._manager.status(tool, system_path=self._system_path(tool))
        if error and status.state is ToolState.MISSING:
            return ToolStatus(**{**status.__dict__, "error": error})
        return status

    def snapshot(self) -> list[dict[str, Any]]:
        """Serialise every tool for the UI."""
        return [
            {
                "tool": status.tool,
                "state": status.state.value,
                "available": status.available,
                "can_install": status.can_install,
                "version": status.version,
                "size_bytes": status.size_bytes,
                "licence": status.licence,
                "source": status.source,
                "purpose": TOOL_PURPOSE.get(status.tool, ""),
                "error": status.error,
            }
            for status in (self.status(name) for name in self.known_tools)
        ]

    # -- installation ----------------------------------------------------

    def start_install(self, tool: str) -> None:
        """Begin an install on a worker thread.

        Raises:
            MediaDownloaderError: if the tool cannot be installed here, or one
                is already in progress.
        """
        status = self.status(tool)
        if status.state is ToolState.INSTALLING:
            raise ToolInstallError(f"{tool} is already being installed.")
        if status.available:
            return
        if not status.can_install:
            raise ToolInstallError(
                f"{tool} cannot be installed automatically on this system yet.",
                hint="Install it with your system package manager instead.",
            )

        with self._lock:
            self._installing.add(tool)
            self._errors.pop(tool, None)

        logger.info("install %s requested", tool)
        thread = threading.Thread(
            target=self._run, args=(tool,), daemon=True, name=f"install-{tool}"
        )
        self._threads.append(thread)
        thread.start()

    def _run(self, tool: str) -> None:
        """Install one tool. Never raises; failures are recorded for the UI."""
        try:
            self._manager.install(tool)
        except BaseException as exc:  # a worker must never let anything escape
            if isinstance(exc, MediaDownloaderError):
                message = exc.message
                logger.warning("install %s failed: %s", tool, message)
            else:
                error_id = record_error(logger, exc, context=f"installing {tool}")
                message = f"Installation failed unexpectedly. Error ID: {error_id}"
            with self._lock:
                self._errors[tool] = message
        finally:
            with self._lock:
                self._installing.discard(tool)

    def wait_for_idle(self, timeout: float | None = None) -> None:
        """Join install threads. Used by tests and shutdown."""
        for thread in list(self._threads):
            thread.join(timeout)
