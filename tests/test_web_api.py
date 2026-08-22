"""API handlers: validation, error mapping and payload shape. No sockets here."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from media_downloader.downloader import DownloadResult, MediaInfo
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.service import Environment
from media_downloader.tools.manager import ToolManager
from media_downloader.web import api
from media_downloader.web.jobs import JobManager, JobState
from media_downloader.web.tools import ToolInstaller

SAMPLE_INFO = MediaInfo(
    title="Example", uploader="Someone", duration_seconds=5, extractor="Twitter", webpage_url=""
)


class FakeDownloader:
    def __init__(self, result_path: Path, block: threading.Event | None = None, **_: Any) -> None:
        self.result_path = result_path
        self.block = block

    def download(self, request: Any) -> DownloadResult:
        if self.block is not None:
            self.block.wait(timeout=5)
        return DownloadResult(path=self.result_path, info=SAMPLE_INFO)


def make_context(
    tmp_path: Path, *, ffmpeg: bool = True, block: threading.Event | None = None
) -> api.ApiContext:
    env = Environment(
        ffmpeg=(
            FFmpegStatus(ffmpeg=tmp_path / "ffmpeg", ffprobe=tmp_path / "ffprobe")
            if ffmpeg
            else FFmpegStatus(ffmpeg=None, ffprobe=None)
        ),
        js_runtime=JSRuntimeStatus(name="deno", path="/usr/bin/deno"),
    )
    manager = JobManager(
        lambda **hooks: FakeDownloader(tmp_path / "Example - 1.mp4", block=block, **hooks)
    )
    return api.ApiContext(
        jobs=manager,
        environment=env,
        download_dir=tmp_path / "out",
        tools=ToolInstaller(_offline_tool_manager(tmp_path)),
    )


def _offline_tool_manager(tmp_path: Path) -> ToolManager:
    """A tool manager that can never reach the network, for API tests."""
    return ToolManager(
        env={"XDG_DATA_HOME": str(tmp_path / "data")},
        fetcher=lambda url, dest, **kw: pytest.fail("an API test tried to download"),
        platform_name=lambda: "linux",
        machine=lambda: "x86_64",
    )


# -- config -------------------------------------------------------------


def test_config_tells_the_frontend_what_it_needs(tmp_path: Path) -> None:
    status, body = api.get_config(make_context(tmp_path))
    assert status == 200
    assert body["quality_choices"][0] == "best"
    assert "mp3" in body["audio_formats"]
    assert body["supported_services"]
    assert body["download_dir"] == str(tmp_path / "out")
    assert body["ffmpeg_available"] is True


# -- creating a download -------------------------------------------------


def test_a_valid_request_is_accepted(tmp_path: Path) -> None:
    status, body = api.create_download(make_context(tmp_path), {"url": "https://x.com/a/status/1"})
    assert status == 202
    assert body["state"] in {s.value for s in JobState}
    assert body["service"] == "X / Twitter"
    assert body["id"]


@pytest.mark.parametrize(
    "body",
    [{}, {"url": 123}, {"url": None}],
)
def test_a_missing_url_is_rejected(tmp_path: Path, body: dict[str, Any]) -> None:
    status, payload = api.create_download(make_context(tmp_path), body)
    assert status == 400
    assert payload["error"]["code"] == "INVALID_URL"


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x", ""],
)
def test_malformed_urls_are_rejected(tmp_path: Path, url: str) -> None:
    status, payload = api.create_download(make_context(tmp_path), {"url": url})
    assert status == 400
    assert payload["error"]["code"] == "INVALID_URL"


@pytest.mark.parametrize(
    ("field", "value"),
    [("quality", "8k"), ("quality", "../etc"), ("audio_format", "exe"), ("audio_format", 7)],
)
def test_unsupported_choices_are_rejected(tmp_path: Path, field: str, value: Any) -> None:
    body = {"url": "https://x.com/a/status/1", field: value}
    status, payload = api.create_download(make_context(tmp_path), body)
    assert status == 400
    assert payload["error"]["code"] == "INVALID_REQUEST"


def test_audio_conversion_without_ffmpeg_fails_before_any_download(tmp_path: Path) -> None:
    """The user learns immediately, instead of watching a doomed progress bar."""
    ctx = make_context(tmp_path, ffmpeg=False)
    status, payload = api.create_download(
        ctx, {"url": "https://x.com/a/status/1", "audio_only": True, "audio_format": "mp3"}
    )
    assert status == 400
    assert payload["error"]["code"] == "FFMPEG_REQUIRED"
    assert not ctx.jobs.history()


def test_a_second_download_is_refused_with_conflict(tmp_path: Path) -> None:
    block = threading.Event()
    ctx = make_context(tmp_path, block=block)
    assert api.create_download(ctx, {"url": "https://x.com/a/status/1"})[0] == 202

    status, payload = api.create_download(ctx, {"url": "https://x.com/b/status/2"})
    assert status == 409
    assert payload["error"]["code"] == "DOWNLOAD_IN_PROGRESS"
    assert payload["error"]["hint"]
    block.set()


def test_the_browser_cannot_choose_where_files_land(tmp_path: Path) -> None:
    """Any output-ish field in the body must be ignored entirely."""
    ctx = make_context(tmp_path)
    status, _ = api.create_download(
        ctx,
        {
            "url": "https://x.com/a/status/1",
            "output": "/etc",
            "output_dir": "/etc",
            "filename": "../../escape.mp4",
        },
    )
    assert status == 202
    job = ctx.jobs.history()[0]
    assert job.request.output_dir == (tmp_path / "out").resolve()
    assert job.request.filename_template is None


def test_notices_are_returned_for_the_ui(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, ffmpeg=False)
    _, body = api.create_download(ctx, {"url": "https://x.com/a/status/1"})
    assert any("FFmpeg" in notice["message"] for notice in body["notices"])


# -- reading jobs --------------------------------------------------------


def test_an_unknown_job_is_a_404(tmp_path: Path) -> None:
    status, payload = api.get_download(make_context(tmp_path), "nope")
    assert status == 404
    assert payload["error"]["code"] == "NOT_FOUND"


def test_a_job_snapshot_has_the_shape_the_ui_expects(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    _, created = api.create_download(ctx, {"url": "https://x.com/a/status/1"})
    status, body = api.get_download(ctx, created["id"])
    assert status == 200
    assert set(body) >= {"id", "state", "url", "title", "progress", "result", "error"}
    assert set(body["progress"]) >= {"percent", "downloaded_bytes", "total_bytes", "speed_bps"}


def test_history_is_listed(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    api.create_download(ctx, {"url": "https://x.com/a/status/1"})
    status, body = api.list_downloads(ctx)
    assert status == 200
    assert len(body["downloads"]) == 1


# -- open folder ---------------------------------------------------------


def test_open_folder_targets_only_the_configured_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr(api, "open_folder", opened.append)

    status, body = api.open_download_folder(ctx)
    assert status == 204
    assert body is None
    assert opened == [ctx.download_dir]


def test_open_folder_takes_no_caller_supplied_path() -> None:
    """There is deliberately no way to ask this server to open any other path."""
    import inspect

    params = list(inspect.signature(api.open_download_folder).parameters)
    assert params == ["ctx"]


def test_open_folder_reports_failure_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_downloader.errors import OutputError

    ctx = make_context(tmp_path)
    monkeypatch.setattr(
        api, "open_folder", lambda p: (_ for _ in ()).throw(OutputError("no file manager"))
    )
    status, payload = api.open_download_folder(ctx)
    assert status == 500
    assert payload is not None
    assert payload["error"]["code"] == "OUTPUT_ERROR"
