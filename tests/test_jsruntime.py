"""JavaScript runtime detection for yt-dlp's YouTube challenge support."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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


# -- canonical description (Defect 2) ------------------------------------
#
# The startup log and the support report used to derive the runtime
# independently and disagree: the log read the implementation name while the
# report took the version from the tool manifest, producing "system 2.9.5" on
# a machine whose runtime was Node 22. Both now go through describe().


def _fake_version(monkeypatch: pytest.MonkeyPatch, stdout: str, code: int = 0) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")

    monkeypatch.setattr(jsruntime_module.subprocess, "run", fake_run)


def test_a_system_node_is_described_with_its_own_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_version(monkeypatch, "v22.20.0\n")
    status = JSRuntimeStatus(name="node", path="/usr/bin/node")
    assert status.describe(jsruntime_module.runtime_version(status)) == "system node 22.20.0"


def test_a_managed_deno_is_described_as_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_version(monkeypatch, "deno 2.9.5 (stable, release, x86_64-unknown-linux-gnu)\n")
    status = JSRuntimeStatus(name="deno", path="/data/tools/deno/deno", managed=True)
    assert status.describe(jsruntime_module.runtime_version(status)) == "managed deno 2.9.5"


def test_a_name_is_never_paired_with_another_candidates_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact defect: node's name beside deno's manifest number."""
    _fake_version(monkeypatch, "v22.20.0\n")
    status = JSRuntimeStatus(name="node", path="/usr/bin/node")
    described = status.describe(jsruntime_module.runtime_version(status))
    assert "2.9.5" not in described


def test_no_runtime_is_described_as_unavailable() -> None:
    status = JSRuntimeStatus(name=None)
    assert status.describe() == "unavailable"
    assert status.describe("2.9.5") == "unavailable"


@pytest.mark.parametrize(
    "outcome",
    [
        {"stdout": "", "code": 0},
        {"stdout": "no version here", "code": 0},
        {"stdout": "v22.20.0", "code": 1},
    ],
)
def test_an_unreadable_version_degrades_to_name_and_source(
    monkeypatch: pytest.MonkeyPatch, outcome: dict[str, Any]
) -> None:
    """A version is a nicety; losing it must not lose the rest."""
    _fake_version(monkeypatch, outcome["stdout"], outcome["code"])
    status = JSRuntimeStatus(name="node", path="/usr/bin/node")
    assert status.describe(jsruntime_module.runtime_version(status)) == "system node"


def test_a_failing_version_probe_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(argv: list[str], **kwargs: Any) -> Any:
        raise OSError("gone")

    monkeypatch.setattr(jsruntime_module.subprocess, "run", explode)
    status = JSRuntimeStatus(name="node", path="/usr/bin/node")
    assert jsruntime_module.runtime_version(status) is None
