"""Diagnostics for people who will never open a terminal.

A packaged application swallows everything a console would have shown. When it
fails on someone else's machine, the only way to find out why is if the
application wrote it down safely and the user can hand it over. So this module
provides three small things:

* a **bounded log file** in the application's own data directory;
* a short **error ID** that appears both in the interface and in the log, so a
  user can say "I got MD-20260823-A1B2C3" and it can be found;
* a **support report** the user chooses to export and send.

Everything is local. Nothing is uploaded, there is no telemetry, and no
diagnostic leaves the machine unless the user sends it themselves.

Redaction is enforced by a logging filter rather than by remembering to be
careful at each call site: the session token, credentials and URL query strings
are scrubbed on the way out, so a careless log call cannot leak them.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import re
import secrets
import sys
import traceback
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from media_downloader import __version__
from media_downloader.paths import ensure_dir, logs_dir

LOG_FILENAME = "media-downloader.log"
#: Bounded on purpose: diagnostics must never quietly eat a user's disk.
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
#: How many log lines a support report may carry.
REPORT_LOG_LINES = 200

_ERROR_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes

#: Substrings that mean a line must never be written verbatim.
_SECRET_KEYS = (
    "x-md-token",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "secret",
)
_REDACTED = "[redacted]"

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
# Redacts to end of line, not just the next token: "Authorization: Bearer xyz"
# would otherwise keep the part that actually matters. Over-redacting a log line
# is a much cheaper mistake than leaking a credential out of a support report.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(" + "|".join(re.escape(k) for k in _SECRET_KEYS) + r")\b\s*[:=].*"
)


def redact_url(url: str) -> str:
    """Reduce a URL to scheme, host and path.

    Query strings and fragments routinely carry signed URLs, expiry tokens and
    session identifiers, and ``user:password@`` appears in the netloc. None of
    that belongs in a file the user is going to email to someone.
    """
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - urlsplit rarely raises
        return _REDACTED
    if not parts.scheme:
        return url
    host = parts.netloc.rpartition("@")[2]
    path = parts.path
    suffix = "?…" if parts.query else ""
    return urlunsplit((parts.scheme, host, path, "", "")) + suffix


def scrub(text: str) -> str:
    """Remove secrets and URL query strings from a line of text."""
    cleaned = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    return _URL_PATTERN.sub(lambda m: redact_url(m.group(0)), cleaned)


class RedactingFilter(logging.Filter):
    """Scrubs every record on its way to the file.

    A filter rather than a convention: it cannot be forgotten at a call site,
    and it also covers messages formatted by third-party code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub(str(record.getMessage()))
            record.args = ()
        except Exception:  # pragma: no cover - never break logging
            record.msg = _REDACTED
            record.args = ()
        return True


def new_error_id(now: datetime | None = None) -> str:
    """A short identifier a user can read aloud, e.g. ``MD-20260823-A1B2C3``."""
    stamp = (now or datetime.now(tz=timezone.utc)).strftime("%Y%m%d")
    suffix = "".join(secrets.choice(_ERROR_ID_ALPHABET) for _ in range(6))
    return f"MD-{stamp}-{suffix}"


@dataclass
class LastError:
    """The most recent unexpected failure, for the support report."""

    error_id: str
    error_type: str
    message: str
    when: str


@dataclass
class DiagnosticsState:
    """Process-wide diagnostics, deliberately tiny."""

    log_file: Path | None = None
    last_error: LastError | None = None
    extra: dict[str, Any] = field(default_factory=dict)


STATE = DiagnosticsState()


def is_frozen() -> bool:
    """True when running from a packaged build."""
    return bool(getattr(sys, "frozen", False))


def configure_file_logging(
    env: dict[str, str] | None = None, *, level: int = logging.INFO
) -> Path | None:
    """Attach a bounded rotating log file to the application logger.

    Returns the log path, or ``None`` if logging could not be set up -- which is
    never fatal: an application that cannot write a log must still run.
    """
    from media_downloader.logging_setup import LOGGER_NAME

    try:
        directory = ensure_dir(logs_dir(env))
        path = directory / LOG_FILENAME
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        handler.setLevel(level)

        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(min(logger.level or level, level))
        # Replace an existing file handler rather than stacking duplicates.
        for existing in list(logger.handlers):
            if isinstance(existing, logging.handlers.RotatingFileHandler):
                logger.removeHandler(existing)
        logger.addHandler(handler)

        STATE.log_file = path
        return path
    except Exception:  # diagnostics must never stop the application
        return None


def log_startup(logger: logging.Logger) -> None:
    """Record what the machine is, which is half of any bug report."""
    # Suppressed on purpose: failing to write a startup line must never stop
    # the application from starting.
    with suppress(Exception):
        logger.info(
            "startup version=%s frozen=%s python=%s os=%s arch=%s",
            __version__,
            is_frozen(),
            platform.python_version(),
            platform.system(),
            platform.machine(),
        )


def record_error(
    logger: logging.Logger, exc: BaseException, *, context: str = "", unexpected: bool = True
) -> str:
    """Log a failure under a fresh error ID and return that ID.

    The traceback is written only for unexpected internal errors; an ordinary
    "this video is private" does not need one.
    """
    error_id = new_error_id()
    try:
        message = getattr(exc, "message", None) or str(exc)
        STATE.last_error = LastError(
            error_id=error_id,
            error_type=type(exc).__name__,
            message=scrub(str(message)),
            when=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        )
        logger.error(
            "%s error_id=%s type=%s message=%s",
            context or "error",
            error_id,
            type(exc).__name__,
            message,
        )
        if unexpected:
            logger.debug(
                "error_id=%s traceback:\n%s",
                error_id,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
    except Exception:  # pragma: no cover - reporting must not raise
        pass
    return error_id


def recent_log_lines(limit: int = REPORT_LOG_LINES) -> list[str]:
    """The tail of the log, bounded and already scrubbed on write."""
    path = STATE.log_file
    if path is None or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return [scrub(line.rstrip("\n")) for line in handle.readlines()[-limit:]]
    except OSError:
        return []


def build_support_report(
    *,
    download_dir: Path | None = None,
    ffmpeg_summary: str = "unknown",
    js_summary: str = "unknown",
    env: dict[str, str] | None = None,
) -> str:
    """A plain-text snapshot the user can send to whoever maintains this.

    Deliberately narrow: version and platform facts, where things are, what the
    last failure was, and a bounded tail of the log. Never the session token,
    cookies, credentials, request headers or the environment.
    """
    from media_downloader.paths import app_data_dir

    lines = [
        "Media Downloader diagnostics",
        "=" * 40,
        f"Version:      {__version__}",
        f"Frozen:       {'yes' if is_frozen() else 'no'}",
        f"OS:           {platform.system()} {platform.release()}",
        f"Architecture: {platform.machine()}",
        f"Python:       {platform.python_version()}",
        f"yt-dlp:       {_ytdlp_version()}",
        f"FFmpeg:       {ffmpeg_summary}",
        f"JS runtime:   {js_summary}",
        f"Downloads:    {download_dir if download_dir else 'unknown'}",
        f"App data:     {app_data_dir(env)}",
        f"Log file:     {STATE.log_file or 'not available'}",
    ]

    last = STATE.last_error
    if last is not None:
        lines += [
            "",
            "Last error",
            "-" * 40,
            f"Error ID:   {last.error_id}",
            f"Type:       {last.error_type}",
            f"Message:    {last.message}",
            f"When:       {last.when}",
        ]

    log = recent_log_lines()
    lines += ["", f"Recent log (last {len(log)} lines)", "-" * 40]
    lines += log or ["(no log entries available)"]
    lines += ["", "This report was generated locally and is not sent anywhere."]
    return "\n".join(lines) + "\n"


def report_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(tz=timezone.utc)).strftime("%Y-%m-%d")
    return f"media-downloader-diagnostics-{stamp}.txt"


def _ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__ as ytdlp

        return str(ytdlp)
    except Exception:  # pragma: no cover - yt-dlp is a hard dependency
        return "unknown"


def describe_environment() -> dict[str, Any]:
    """Machine-readable facts, shared by the API and artifact metadata."""
    return {
        "version": __version__,
        "frozen": is_frozen(),
        "python": platform.python_version(),
        "os": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "yt_dlp": _ytdlp_version(),
        "log_file": str(STATE.log_file) if STATE.log_file else None,
        "pid": os.getpid(),
    }
