"""Diagnostics: bounded logging, redaction, error IDs and the support report.

The redaction tests matter most. A support report is a file a non-technical
user emails to a stranger, so anything secret that reaches it is a real leak.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from media_downloader import diagnostics
from media_downloader.diagnostics import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    RedactingFilter,
    build_support_report,
    configure_file_logging,
    new_error_id,
    recent_log_lines,
    record_error,
    redact_url,
    report_filename,
    scrub,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test out of the developer's real app-data directory."""
    monkeypatch.setattr("media_downloader.paths.current_platform", lambda: "linux")
    monkeypatch.setattr(diagnostics, "STATE", diagnostics.DiagnosticsState())
    return {"XDG_DATA_HOME": str(tmp_path / "data")}


# -- redaction ----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a/b", "https://example.com/a/b"),
        ("https://example.com/a?token=SECRET", "https://example.com/a?…"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://user:pw@example.com/a", "https://example.com/a"),
        ("https://user:pw@example.com/a?sig=X#f", "https://example.com/a?…"),
    ],
)
def test_urls_lose_credentials_query_and_fragment(url: str, expected: str) -> None:
    """Signed URLs carry expiring tokens in the query; none of it may survive."""
    assert redact_url(url) == expected


@pytest.mark.parametrize(
    "secret",
    ["SUPERSECRETTOKEN", "abc123deadbeef"],
)
@pytest.mark.parametrize(
    "template",
    [
        "X-MD-Token: {}",
        "x-md-token={}",
        "Authorization: Bearer {}",
        "Cookie: session={}",
        "password={}",
        "api_key: {}",
        "access_token = {}",
    ],
)
def test_secrets_are_scrubbed(template: str, secret: str) -> None:
    assert secret not in scrub(template.format(secret))


def test_scrub_keeps_ordinary_text_readable() -> None:
    assert scrub("job abc123 state=downloading") == "job abc123 state=downloading"


def test_the_filter_scrubs_records_not_just_call_sites(tmp_path: Path) -> None:
    """A careless log call must not be able to leak; the filter is the guard."""
    record = logging.LogRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "connecting with X-MD-Token: LEAKED to https://a.test/x?sig=ALSOLEAKED",
        (),
        None,
    )
    RedactingFilter().filter(record)
    text = record.getMessage()
    assert "LEAKED" not in text
    assert "ALSOLEAKED" not in text


# -- logging ------------------------------------------------------------


def test_logging_writes_into_the_app_data_directory(
    _isolated_state: dict[str, str], tmp_path: Path
) -> None:
    path = configure_file_logging(_isolated_state)
    assert path is not None
    assert path.parent == tmp_path / "data" / "media-downloader" / "logs"
    assert path.name == "media-downloader.log"


def test_logging_is_bounded() -> None:
    """A diagnostic log must never quietly consume a user's disk."""
    assert 0 < LOG_MAX_BYTES <= 8 * 1024 * 1024
    assert 1 <= LOG_BACKUP_COUNT <= 5


def test_the_handler_rotates(_isolated_state: dict[str, str]) -> None:
    import logging.handlers

    from media_downloader.logging_setup import LOGGER_NAME

    configure_file_logging(_isolated_state)
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == LOG_MAX_BYTES
    assert handlers[0].backupCount == LOG_BACKUP_COUNT


def test_configuring_twice_does_not_stack_handlers(_isolated_state: dict[str, str]) -> None:
    import logging.handlers

    from media_downloader.logging_setup import LOGGER_NAME

    configure_file_logging(_isolated_state)
    configure_file_logging(_isolated_state)
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1


def test_a_log_that_cannot_be_written_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """An application that cannot log must still run."""
    monkeypatch.setattr(
        diagnostics, "ensure_dir", lambda p: (_ for _ in ()).throw(OSError("read-only"))
    )
    assert configure_file_logging({}) is None


def test_a_written_secret_never_reaches_the_file(_isolated_state: dict[str, str]) -> None:
    from media_downloader.logging_setup import get_logger

    path = configure_file_logging(_isolated_state)
    assert path is not None
    get_logger("t").info("token X-MD-Token: TOPSECRET url https://a.test/x?sig=NOPE")
    for handler in logging.getLogger("media_downloader").handlers:
        handler.flush()

    written = path.read_text()
    assert "TOPSECRET" not in written
    assert "NOPE" not in written


# -- error IDs ----------------------------------------------------------


def test_error_ids_look_like_something_a_person_can_read_out() -> None:
    error_id = new_error_id(datetime(2026, 8, 23, tzinfo=timezone.utc))
    assert error_id.startswith("MD-20260823-")
    assert len(error_id) == len("MD-20260823-ABCDEF")
    # No look-alike characters that get misheard on a phone.
    assert not set(error_id.rsplit("-", 1)[1]) & set("O0I1")


def test_error_ids_are_unique() -> None:
    assert len({new_error_id() for _ in range(200)}) > 190


def test_recording_an_error_logs_the_same_id_it_returns(
    _isolated_state: dict[str, str],
) -> None:
    """This is the whole point: the user quotes the ID and it is findable."""
    from media_downloader.logging_setup import get_logger

    path = configure_file_logging(_isolated_state)
    assert path is not None
    logger = get_logger("t")

    error_id = record_error(logger, RuntimeError("boom"), context="testing")
    for handler in logging.getLogger("media_downloader").handlers:
        handler.flush()

    assert error_id in path.read_text()
    assert diagnostics.STATE.last_error is not None
    assert diagnostics.STATE.last_error.error_id == error_id


def test_recording_an_error_never_raises(_isolated_state: dict[str, str]) -> None:
    class HostileError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("even __str__ is broken")

    from media_downloader.logging_setup import get_logger

    assert record_error(get_logger("t"), HostileError()).startswith("MD-")


# -- support report -----------------------------------------------------


def test_the_report_answers_the_questions_a_maintainer_would_ask(
    _isolated_state: dict[str, str], tmp_path: Path
) -> None:
    configure_file_logging(_isolated_state)
    report = build_support_report(
        download_dir=tmp_path / "dl",
        ffmpeg_summary="managed n9.0.1",
        js_summary="system",
        env=_isolated_state,
    )
    for expected in (
        "Version:",
        "Frozen:",
        "OS:",
        "Architecture:",
        "Python:",
        "yt-dlp:",
        "FFmpeg:",
        "JS runtime:",
        "Downloads:",
        "App data:",
    ):
        assert expected in report
    assert "managed n9.0.1" in report
    assert "not sent anywhere" in report


def test_the_report_includes_the_last_error(_isolated_state: dict[str, str]) -> None:
    from media_downloader.logging_setup import get_logger

    configure_file_logging(_isolated_state)
    error_id = record_error(get_logger("t"), ValueError("something specific"))
    report = build_support_report(env=_isolated_state)
    assert error_id in report
    assert "ValueError" in report


def test_the_report_excludes_secrets(_isolated_state: dict[str, str]) -> None:
    """The user is going to email this file to someone."""
    from media_downloader.logging_setup import get_logger

    configure_file_logging(_isolated_state)
    logger = get_logger("t")
    logger.info("X-MD-Token: LEAKTOKEN")
    logger.info("Cookie: session=LEAKCOOKIE")
    logger.info("fetching https://a.test/v?signature=LEAKSIG")
    for handler in logging.getLogger("media_downloader").handlers:
        handler.flush()

    report = build_support_report(env=_isolated_state)
    for secret in ("LEAKTOKEN", "LEAKCOOKIE", "LEAKSIG"):
        assert secret not in report


def test_the_log_excerpt_is_bounded(_isolated_state: dict[str, str]) -> None:
    from media_downloader.logging_setup import get_logger

    configure_file_logging(_isolated_state)
    logger = get_logger("t")
    for i in range(500):
        logger.info("line %s", i)
    for handler in logging.getLogger("media_downloader").handlers:
        handler.flush()

    assert len(recent_log_lines()) <= diagnostics.REPORT_LOG_LINES


def test_a_report_works_before_any_logging_exists() -> None:
    """Diagnostics must not require a working log to produce something."""
    report = build_support_report(env={})
    assert "Media Downloader diagnostics" in report
    assert "no log entries available" in report


def test_the_report_filename_is_dated() -> None:
    assert report_filename(datetime(2026, 8, 23, tzinfo=timezone.utc)) == (
        "media-downloader-diagnostics-2026-08-23.txt"
    )
