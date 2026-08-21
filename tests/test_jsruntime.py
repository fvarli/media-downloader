"""JavaScript runtime detection for yt-dlp's YouTube challenge support."""

from __future__ import annotations

import pytest

from media_downloader import jsruntime as jsruntime_module
from media_downloader.jsruntime import (
    JSRuntimeStatus,
    detect_js_runtime,
    js_runtimes_option,
)

# Verified against yt_dlp.globals.supported_js_runtimes.
SUPPORTED_BY_YT_DLP = ("deno", "node", "bun", "quickjs")


@pytest.mark.parametrize("runtime", ["deno", "node", "bun"])
def test_detects_each_supported_runtime(runtime: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jsruntime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == runtime else None,
    )
    status = detect_js_runtime()
    assert status.available
    assert status.name == runtime


def test_prefers_deno_when_several_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsruntime_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert detect_js_runtime().name == "deno"


def test_falls_back_to_the_deno_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsruntime_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(jsruntime_module.importlib.util, "find_spec", lambda name: object())
    status = detect_js_runtime()
    assert status.available
    assert status.from_package


def test_reports_unavailable_when_nothing_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An nvm-managed node absent from this process's PATH must not count."""
    monkeypatch.setattr(jsruntime_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(jsruntime_module.importlib.util, "find_spec", lambda name: None)
    status = detect_js_runtime()
    assert not status.available
    assert status.name is None


def test_deno_needs_no_explicit_enabling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deno is the one runtime yt-dlp turns on by itself."""
    monkeypatch.setattr(
        jsruntime_module.shutil,
        "which",
        lambda name: "/usr/bin/deno" if name == "deno" else None,
    )
    status = detect_js_runtime()
    assert not status.needs_explicit_enabling
    assert js_runtimes_option(status) is None


@pytest.mark.parametrize("runtime", ["node", "bun"])
def test_other_runtimes_must_be_enabled_explicitly(
    runtime: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, an installed Node is silently ignored by yt-dlp."""
    monkeypatch.setattr(
        jsruntime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == runtime else None,
    )
    status = detect_js_runtime()
    assert status.needs_explicit_enabling

    option = js_runtimes_option(status)
    assert option is not None
    assert runtime in option
    # Deno stays enabled so installing it later keeps working.
    assert "deno" in option


def test_no_runtime_means_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsruntime_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(jsruntime_module.importlib.util, "find_spec", lambda name: None)
    assert js_runtimes_option(detect_js_runtime()) is None


def test_generated_option_matches_yt_dlps_expected_shape() -> None:
    """yt-dlp requires a dict of {runtime_name: {config}}."""
    option = js_runtimes_option(JSRuntimeStatus(name="node", path="/usr/bin/node"))
    assert option is not None
    assert all(isinstance(config, dict) for config in option.values())
    assert set(option) <= set(SUPPORTED_BY_YT_DLP)
