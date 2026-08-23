"""The tool-install API and its consent semantics. Nothing downloads here."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import MappingProxyType

import pytest

from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.service import Environment
from media_downloader.tools import manifest
from media_downloader.tools.manager import ToolManager
from media_downloader.web import api
from media_downloader.web import tools as web_tools
from media_downloader.web.jobs import JobManager
from media_downloader.web.tools import ToolInstaller


@pytest.fixture(autouse=True)
def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("media_downloader.paths.current_platform", lambda: "linux")


def make_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    system_ffmpeg: bool = False,
    system_js: bool = False,
    payload: bytes | None = None,
    fail: Exception | None = None,
) -> tuple[ToolInstaller, list[str]]:
    """An installer whose system detection and network are both controlled."""
    fetched: list[str] = []

    def fetcher(url: str, destination: Path, *, max_bytes: int) -> None:
        fetched.append(url)
        if fail is not None:
            raise fail
        assert payload is not None
        destination.write_bytes(payload)

    monkeypatch.setattr(
        web_tools,
        "detect_ffmpeg",
        lambda *a, **k: FFmpegStatus(
            ffmpeg=Path("/usr/bin/ffmpeg") if system_ffmpeg else None,
            ffprobe=Path("/usr/bin/ffprobe") if system_ffmpeg else None,
        ),
    )
    monkeypatch.setattr(
        web_tools,
        "detect_js_runtime",
        lambda: (
            JSRuntimeStatus(name="node", path="/usr/bin/node")
            if system_js
            else JSRuntimeStatus(name=None)
        ),
    )
    manager = ToolManager(
        env={"XDG_DATA_HOME": str(tmp_path / "data")},
        fetcher=fetcher,
        platform_name=lambda: "linux",
        machine=lambda: "x86_64",
    )
    return ToolInstaller(manager), fetched


def context(tmp_path: Path, installer: ToolInstaller) -> api.ApiContext:
    return api.ApiContext(
        jobs=JobManager(lambda **kw: None),
        environment=Environment(FFmpegStatus(None, None), JSRuntimeStatus(None)),
        download_dir=tmp_path / "out",
        tools=installer,
    )


def pinned_payload(tmp_path: Path, entries: dict[str, bytes]) -> bytes:
    """A zip whose checksum matches what we patch into the manifest."""
    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return archive.read_bytes()


def patch_manifest(
    monkeypatch: pytest.MonkeyPatch, tool: str, data: bytes, members: dict[str, str]
) -> None:
    """Point the manifest at a local payload, keeping every rule intact."""
    spec = manifest.ToolSpec(
        tool=tool,
        version="test-1",
        url="https://example.invalid/tool.zip",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        members=MappingProxyType(members),
        licence="TEST",
        source="https://example.invalid",
    )
    monkeypatch.setitem(manifest._MANIFEST, (tool, "linux", "x86_64"), spec)


# -- querying -----------------------------------------------------------


def test_listing_tools_never_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer, fetched = make_installer(tmp_path, monkeypatch)
    status, body = api.get_tools(context(tmp_path, installer))

    assert status == 200
    assert {t["tool"] for t in body["tools"]} == {"ffmpeg", "deno"}
    assert fetched == []


def test_each_tool_explains_why_it_is_wanted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user has to be told what they are consenting to."""
    installer, _ = make_installer(tmp_path, monkeypatch)
    _, body = api.get_tools(context(tmp_path, installer))
    for entry in body["tools"]:
        assert entry["purpose"]
        assert entry["can_install"] is True
        assert entry["size_bytes"] and entry["licence"] and entry["source"]


def test_a_system_tool_is_reported_and_not_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, _ = make_installer(tmp_path, monkeypatch, system_ffmpeg=True, system_js=True)
    _, body = api.get_tools(context(tmp_path, installer))
    for entry in body["tools"]:
        assert entry["state"] == "system"
        assert entry["available"] is True
        assert entry["can_install"] is False


def test_macos_offers_deno_but_never_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS has a verified Deno source but no acceptable FFmpeg one.

    The UI must reflect that honestly: no install button for a target we have
    no trustworthy binary for.
    """
    installer, _ = make_installer(tmp_path, monkeypatch)
    installer._manager = ToolManager(
        env={"XDG_DATA_HOME": str(tmp_path / "d")},
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "darwin",
        machine=lambda: "arm64",
    )
    _, body = api.get_tools(context(tmp_path, installer))
    entries = {t["tool"]: t for t in body["tools"]}

    assert entries["ffmpeg"]["state"] == "unsupported"
    assert entries["ffmpeg"]["can_install"] is False
    assert entries["deno"]["state"] == "missing"
    assert entries["deno"]["can_install"] is True


def test_an_unsupported_platform_is_reported_not_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, _ = make_installer(tmp_path, monkeypatch)
    installer._manager = ToolManager(
        env={"XDG_DATA_HOME": str(tmp_path / "d")},
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "linux",
        machine=lambda: "aarch64",
    )
    _, body = api.get_tools(context(tmp_path, installer))
    for entry in body["tools"]:
        assert entry["state"] == "unsupported"
        assert entry["can_install"] is False


# -- installing ---------------------------------------------------------


def test_installing_requires_an_explicit_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is fetched until the install endpoint is actually called."""
    data = pinned_payload(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    patch_manifest(monkeypatch, "ffmpeg", data, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})
    installer, fetched = make_installer(tmp_path, monkeypatch, payload=data)
    ctx = context(tmp_path, installer)

    api.get_tools(ctx)
    assert fetched == []

    status, _ = api.install_tool(ctx, "ffmpeg")
    installer.wait_for_idle(timeout=5)

    assert status == 202
    assert fetched == ["https://example.invalid/tool.zip"]
    assert installer.status("ffmpeg").available


def test_an_unknown_tool_is_a_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route segment is validated against a fixed set."""
    installer, fetched = make_installer(tmp_path, monkeypatch)
    for name in ["curl", "../../etc/passwd", "", "FFMPEG"]:
        status, body = api.install_tool(context(tmp_path, installer), name)
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"
    assert fetched == []


def test_a_failed_install_is_reported_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_downloader.tools.manager import ToolInstallError

    data = pinned_payload(tmp_path, {"deno": b"D"})
    patch_manifest(monkeypatch, "deno", data, {"deno": "deno"})
    installer, _ = make_installer(
        tmp_path, monkeypatch, payload=data, fail=ToolInstallError("network unreachable")
    )
    ctx = context(tmp_path, installer)

    api.install_tool(ctx, "deno")
    installer.wait_for_idle(timeout=5)

    _, body = api.get_tools(ctx)
    entry = next(t for t in body["tools"] if t["tool"] == "deno")
    assert entry["state"] == "missing"
    assert "network unreachable" in entry["error"]
    # Still offered, so the user can try again.
    assert entry["can_install"] is True


def test_installing_an_already_available_tool_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, fetched = make_installer(tmp_path, monkeypatch, system_ffmpeg=True)
    status, _ = api.install_tool(context(tmp_path, installer), "ffmpeg")
    assert status == 202
    assert fetched == []


def test_declining_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not asking must leave the tool exactly as it was, with no side effects."""
    installer, fetched = make_installer(tmp_path, monkeypatch)
    ctx = context(tmp_path, installer)
    before = api.get_tools(ctx)[1]
    after = api.get_tools(ctx)[1]
    assert before == after
    assert fetched == []


# --- diagnostics API ----------------------------------------------------


def test_diagnostics_report_is_generated_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, _ = make_installer(tmp_path, monkeypatch)
    status, body = api.get_diagnostics(context(tmp_path, installer))

    assert status == 200
    assert "Media Downloader diagnostics" in body["report"]
    assert body["environment"]["version"]
    assert body["filename"].startswith("media-downloader-diagnostics-")


def test_diagnostics_report_never_contains_the_session_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_downloader.diagnostics import configure_file_logging
    from media_downloader.logging_setup import get_logger

    monkeypatch.setattr("media_downloader.paths.current_platform", lambda: "linux")
    configure_file_logging({"XDG_DATA_HOME": str(tmp_path / "data")})
    get_logger("t").info("X-MD-Token: SESSIONTOKENLEAK")

    installer, _ = make_installer(tmp_path, monkeypatch)
    _, body = api.get_diagnostics(context(tmp_path, installer))
    assert "SESSIONTOKENLEAK" not in body["report"]


def test_exporting_writes_a_file_the_user_can_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, _ = make_installer(tmp_path, monkeypatch)
    ctx = context(tmp_path, installer)
    status, body = api.export_diagnostics(ctx)

    assert status == 200
    written = ctx.download_dir / body["filename"]
    assert written.is_file()
    assert "Media Downloader diagnostics" in written.read_text()


def test_open_logs_takes_no_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    called: list[bool] = []
    monkeypatch.setattr(api, "open_log_folder", lambda: called.append(True))
    installer, _ = make_installer(tmp_path, monkeypatch)

    status, body = api.open_logs(context(tmp_path, installer))
    assert status == 204 and body is None
    assert called == [True]
    assert list(inspect.signature(api.open_logs).parameters) == ["ctx"]


def test_an_unexpected_install_failure_gets_an_error_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user should be able to quote a code rather than describe a symptom."""
    data = pinned_payload(tmp_path, {"deno": b"D"})
    patch_manifest(monkeypatch, "deno", data, {"deno": "deno"})
    installer, _ = make_installer(
        tmp_path, monkeypatch, payload=data, fail=RuntimeError("disk exploded")
    )
    ctx = context(tmp_path, installer)

    api.install_tool(ctx, "deno")
    installer.wait_for_idle(timeout=5)

    _, body = api.get_tools(ctx)
    entry = next(t for t in body["tools"] if t["tool"] == "deno")
    assert "MD-" in (entry["error"] or "")
    # The raw internal message is not shown to the user.
    assert "disk exploded" not in (entry["error"] or "")
