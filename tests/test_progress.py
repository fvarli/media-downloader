"""Progress reporting: Rich when interactive, plain text when redirected."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from media_downloader.progress import ProgressReporter, _describe


def make_console(*, terminal: bool) -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=terminal, width=100), buffer


def downloading(**overrides: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "status": "downloading",
        "downloaded_bytes": 500,
        "total_bytes": 1000,
        "filename": "video.mp4",
        "info_dict": {},
    }
    status.update(overrides)
    return status


@pytest.mark.parametrize(
    ("info_dict", "expected"),
    [
        ({"vcodec": "none"}, "Audio"),
        ({"acodec": "none"}, "Video"),
        ({"vcodec": "vp9", "acodec": "opus"}, "Media"),
        ({}, "Media"),
    ],
)
def test_describe_labels_the_stream(info_dict: dict[str, Any], expected: str) -> None:
    assert _describe({"info_dict": info_dict}) == expected


def test_uses_a_rich_bar_on_a_terminal() -> None:
    console, _ = make_console(terminal=True)
    with ProgressReporter(console) as reporter:
        assert reporter._progress is not None
        reporter.hook(downloading())
        assert reporter._task is not None


def test_falls_back_to_plain_lines_when_redirected() -> None:
    """A live bar would only produce noise in a pipe or a log file."""
    console, buffer = make_console(terminal=False)
    with ProgressReporter(console) as reporter:
        assert reporter._progress is None
        reporter.hook(downloading())
    assert "50.0%" in buffer.getvalue()


def test_plain_output_is_throttled() -> None:
    console, buffer = make_console(terminal=False)
    with ProgressReporter(console) as reporter:
        for _ in range(5):
            reporter.hook(downloading())
    assert buffer.getvalue().count("Media:") == 1


def test_plain_output_copes_with_an_unknown_total() -> None:
    console, buffer = make_console(terminal=False)
    with ProgressReporter(console) as reporter:
        reporter.hook(downloading(total_bytes=None, total_bytes_estimate=None))
    assert "500 bytes" in buffer.getvalue()


def test_finished_status_is_reported_in_plain_mode() -> None:
    console, buffer = make_console(terminal=False)
    with ProgressReporter(console) as reporter:
        reporter.hook({"status": "finished", "total_bytes": 1000, "info_dict": {}})
    assert "download complete" in buffer.getvalue()


def test_switching_streams_replaces_the_task() -> None:
    """Video then audio must not stack two bars on top of each other."""
    console, _ = make_console(terminal=True)
    with ProgressReporter(console) as reporter:
        reporter.hook(downloading(filename="video.mp4", info_dict={"acodec": "none"}))
        first = reporter._task
        reporter.hook(downloading(filename="audio.m4a", info_dict={"vcodec": "none"}))
        assert reporter._task != first


def test_disabled_reporter_prints_nothing() -> None:
    console, buffer = make_console(terminal=False)
    with ProgressReporter(console, enabled=False) as reporter:
        reporter.hook(downloading())
        reporter.hook({"status": "finished", "info_dict": {}})
    assert buffer.getvalue() == ""


def test_a_broken_status_never_breaks_the_download() -> None:
    """The hook is called from inside yt-dlp; it must swallow its own errors."""
    console, _ = make_console(terminal=False)
    with ProgressReporter(console) as reporter:
        reporter.hook({"status": "downloading", "info_dict": None, "downloaded_bytes": object()})
        reporter.hook({})


def test_the_live_display_is_stopped_even_after_an_error() -> None:
    console, _ = make_console(terminal=True)
    reporter = ProgressReporter(console)
    with pytest.raises(RuntimeError), reporter:
        reporter.hook(downloading())
        raise RuntimeError("download blew up")
    assert reporter._progress is None
