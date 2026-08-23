"""Endpoint handlers for the web UI.

Handlers are plain functions taking already-parsed input and returning
``(status, payload)``. Keeping HTTP mechanics out of here means the whole API
can be tested by calling functions, with the socket layer covered separately.

The browser never supplies a filesystem path. The output directory is fixed by
the server at startup, and requests carry only a URL and the same choices the
CLI exposes as flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_downloader import __version__
from media_downloader.config import (
    AUDIO_FORMAT_CHOICES,
    LOSSLESS_AUDIO_FORMAT,
    QUALITY_CHOICES,
    build_request,
)
from media_downloader.diagnostics import (
    STATE,
    build_support_report,
    describe_environment,
    report_filename,
)
from media_downloader.errors import MediaDownloaderError
from media_downloader.paths import ensure_dir
from media_downloader.service import Environment, environment_notices
from media_downloader.urls import SUPPORTED_SERVICE_NAMES, detect_service, validate_url
from media_downloader.web.jobs import DownloadInProgressError, JobManager
from media_downloader.web.system import open_folder, open_log_folder
from media_downloader.web.tools import ToolInstaller

#: Exception -> HTTP status for failures detected while handling the request.
#: Failures that happen *during* a download are reported on the job instead,
#: because the POST has already been answered with 202.
_STATUS_FOR_CODE: dict[str, int] = {
    "INVALID_URL": 400,
    "OUTPUT_ERROR": 400,
    "FFMPEG_REQUIRED": 400,
    "DOWNLOAD_IN_PROGRESS": 409,
    "TOOL_INSTALL": 409,
}


@dataclass(frozen=True)
class ApiContext:
    """Everything the handlers need, assembled once at startup."""

    jobs: JobManager
    environment: Environment
    download_dir: Path
    tools: ToolInstaller


def error_payload(exc: MediaDownloaderError) -> tuple[int, dict[str, Any]]:
    """Render an exception as ``(status, body)``."""
    from media_downloader.web.jobs import JobError

    error = JobError.from_exception(exc)
    status = _STATUS_FOR_CODE.get(error.code, 500)
    return status, {"error": {"code": error.code, "message": error.message, "hint": error.hint}}


def get_config(ctx: ApiContext) -> tuple[int, dict[str, Any]]:
    """Everything the frontend needs to render its controls."""
    return 200, {
        "version": __version__,
        "supported_services": list(SUPPORTED_SERVICE_NAMES),
        "quality_choices": list(QUALITY_CHOICES),
        "audio_formats": list(AUDIO_FORMAT_CHOICES),
        "default_audio_format": LOSSLESS_AUDIO_FORMAT,
        "download_dir": str(ctx.download_dir),
        "ffmpeg_available": ctx.environment.ffmpeg.available,
        "js_runtime_available": ctx.environment.js_runtime.available,
    }


def create_download(ctx: ApiContext, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Validate a download request and start it.

    Returns 202 with the job once accepted: the download itself runs on a
    worker thread and is observed by polling the job.
    """
    url = body.get("url")
    if not isinstance(url, str):
        return 400, {
            "error": {
                "code": "INVALID_URL",
                "message": "No URL was provided.",
                "hint": "Paste a public http:// or https:// media link.",
            }
        }

    audio_only = bool(body.get("audio_only", False))
    quality = body.get("quality", "best")
    audio_format = body.get("audio_format", LOSSLESS_AUDIO_FORMAT)

    if quality not in QUALITY_CHOICES:
        return 400, _bad_choice("quality", quality, QUALITY_CHOICES)
    if audio_format not in AUDIO_FORMAT_CHOICES:
        return 400, _bad_choice("audio format", audio_format, AUDIO_FORMAT_CHOICES)

    try:
        # validate_url is a separate step from build_request -- the CLI calls it
        # explicitly too. Without it, file:// and javascript: URLs would reach
        # yt-dlp. The output directory comes from the server, never the browser,
        # so no untrusted path can reach the filesystem either way.
        request = build_request(
            url=validate_url(url),
            output=str(ctx.download_dir),
            quality=str(quality),
            audio_only=audio_only,
            audio_format=str(audio_format),
            env={},
        )
    except MediaDownloaderError as exc:
        return error_payload(exc)

    # Surface the same degradation that would abort the download, before the
    # user watches a progress bar that is going to fail.
    from media_downloader.options import build_ydl_opts

    try:
        build_ydl_opts(request, ctx.environment.ffmpeg)
    except MediaDownloaderError as exc:
        return error_payload(exc)

    try:
        job = ctx.jobs.submit(request)
    except DownloadInProgressError as exc:
        return error_payload(exc)

    service = detect_service(request.url)
    payload = job.snapshot()
    payload["service"] = service.name if service is not None else None
    payload["notices"] = [
        {"level": n.level, "message": n.message}
        for n in environment_notices(request, ctx.environment)
    ]
    return 202, payload


def _bad_choice(label: str, value: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    return {
        "error": {
            "code": "INVALID_REQUEST",
            "message": f"Unsupported {label}: {value!r}.",
            "hint": f"Choose one of: {', '.join(allowed)}.",
        }
    }


def get_download(ctx: ApiContext, job_id: str) -> tuple[int, dict[str, Any]]:
    """Snapshot of one job. This is what the UI polls."""
    job = ctx.jobs.get(job_id)
    if job is None:
        return 404, {
            "error": {
                "code": "NOT_FOUND",
                "message": "That download is not known to this session.",
                "hint": None,
            }
        }
    return 200, job.snapshot()


def list_downloads(ctx: ApiContext) -> tuple[int, dict[str, Any]]:
    """This session's downloads, newest first. Nothing is persisted to disk."""
    return 200, {"downloads": [job.snapshot() for job in ctx.jobs.history()]}


def get_tools(ctx: ApiContext) -> tuple[int, dict[str, Any]]:
    """Report where each optional tool comes from, and whether we can install it.

    This is a pure query: asking never downloads anything.
    """
    return 200, {"tools": ctx.tools.snapshot()}


def install_tool(ctx: ApiContext, tool: str) -> tuple[int, dict[str, Any]]:
    """Begin installing one optional tool, on the user's explicit request.

    The tool name comes from a fixed route segment, never from a request body,
    so the browser cannot name a URL, a version or a filesystem location. It is
    checked against the known set before anything else happens.
    """
    if tool not in ctx.tools.known_tools:
        return 404, {
            "error": {
                "code": "NOT_FOUND",
                "message": f"Unknown tool: {tool}",
                "hint": None,
            }
        }

    try:
        ctx.tools.start_install(tool)
    except MediaDownloaderError as exc:
        return error_payload(exc)

    return 202, {"tools": ctx.tools.snapshot()}


def _tool_summary(ctx: ApiContext, tool: str) -> str:
    """One-line description of where a tool comes from, for the report."""
    status = ctx.tools.status(tool)
    version = f" {status.version}" if status.version else ""
    return f"{status.state.value}{version}"


def get_diagnostics(ctx: ApiContext) -> tuple[int, dict[str, Any]]:
    """A support snapshot the user can read, copy or export.

    Generated locally and returned only to this page. Nothing is uploaded, and
    the report itself excludes the session token, cookies, credentials and
    request headers by construction.
    """
    report = build_support_report(
        download_dir=ctx.download_dir,
        ffmpeg_summary=_tool_summary(ctx, "ffmpeg"),
        js_summary=_tool_summary(ctx, "deno"),
    )
    last = STATE.last_error
    return 200, {
        "environment": describe_environment(),
        "last_error": (
            {"error_id": last.error_id, "type": last.error_type, "when": last.when}
            if last is not None
            else None
        ),
        "report": report,
        "filename": report_filename(),
    }


def export_diagnostics(ctx: ApiContext) -> tuple[int, dict[str, Any]]:
    """Write the support report next to the user's downloads.

    A file they can find and attach to a message, rather than something they
    have to select and copy out of a browser.
    """
    name = report_filename()
    try:
        directory = ensure_dir(ctx.download_dir)
        target = directory / name
        target.write_text(
            build_support_report(
                download_dir=ctx.download_dir,
                ffmpeg_summary=_tool_summary(ctx, "ffmpeg"),
                js_summary=_tool_summary(ctx, "deno"),
            ),
            encoding="utf-8",
        )
    except MediaDownloaderError as exc:
        return error_payload(exc)
    except OSError as exc:
        return 500, {
            "error": {
                "code": "OUTPUT_ERROR",
                "message": f"The report could not be written: {exc}",
                "hint": str(ctx.download_dir),
            }
        }
    return 200, {"filename": name, "path": str(target)}


def open_logs(ctx: ApiContext) -> tuple[int, dict[str, Any] | None]:
    """Open the log directory. Takes no path, by design."""
    try:
        open_log_folder()
    except (MediaDownloaderError, OSError) as exc:
        message = exc.message if isinstance(exc, MediaDownloaderError) else str(exc)
        return 500, {"error": {"code": "OUTPUT_ERROR", "message": message, "hint": None}}
    return 204, None


def open_download_folder(ctx: ApiContext) -> tuple[int, dict[str, Any] | None]:
    """Open the configured download directory in the file manager.

    Takes no arguments by design: there is deliberately no way to ask this
    server to open an arbitrary path.
    """
    try:
        ctx.download_dir.mkdir(parents=True, exist_ok=True)
        open_folder(ctx.download_dir)
    except (MediaDownloaderError, OSError) as exc:
        # Always 500: the request itself was fine, the server could not carry
        # it out. Reusing the request-validation status map would mislabel it.
        message = exc.message if isinstance(exc, MediaDownloaderError) else str(exc)
        hint = exc.hint if isinstance(exc, MediaDownloaderError) else None
        return 500, {
            "error": {
                "code": "OUTPUT_ERROR",
                "message": message,
                "hint": hint or str(ctx.download_dir),
            }
        }
    return 204, None
