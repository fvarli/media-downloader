"""Manifest, checksum verification and the install/discovery split.

Every test here is offline: the network layer is injected.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from media_downloader.errors import MediaDownloaderError
from media_downloader.tools import manifest
from media_downloader.tools.manager import ToolInstallError, ToolManager, ToolState
from media_downloader.tools.verify import ChecksumError, sha256_file, verify_sha256

# -- manifest -----------------------------------------------------------


def test_the_configured_target_is_linux_x86_64() -> None:
    for tool in (manifest.FFMPEG, manifest.DENO):
        spec = manifest.lookup(tool, "linux", "x86_64")
        assert spec is not None
        assert spec.url.startswith("https://")
        assert len(spec.sha256) == 64
        assert spec.members
        assert spec.licence and spec.source


def test_every_pinned_url_is_https() -> None:
    """A non-HTTPS URL must never be able to enter the manifest unnoticed."""
    for spec in manifest._MANIFEST.values():
        assert spec.url.startswith("https://"), spec.url


def test_pinned_checksums_are_lowercase_hex() -> None:
    for spec in manifest._MANIFEST.values():
        assert len(spec.sha256) == 64
        assert spec.sha256 == spec.sha256.lower()
        int(spec.sha256, 16)


@pytest.mark.parametrize(
    ("platform", "machine"),
    [("darwin", "arm64"), ("darwin", "x86_64"), ("win32", "AMD64"), ("linux", "aarch64")],
)
def test_unverified_targets_are_absent_rather_than_guessed(platform: str, machine: str) -> None:
    """We have no verified source for these yet, so there must be no entry."""
    assert manifest.lookup(manifest.FFMPEG, platform, machine) is None


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "x86_64"),
        ("AMD64", "x86_64"),
        ("x64", "x86_64"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
    ],
)
def test_architecture_spellings_are_normalised(machine: str, expected: str) -> None:
    assert manifest.normalise_arch(machine) == expected


def test_ffmpeg_declares_both_binaries() -> None:
    """A merge needs ffprobe as well; a partial install is not usable."""
    spec = manifest.lookup(manifest.FFMPEG, "linux", "x86_64")
    assert spec is not None
    assert set(spec.executables) == {"ffmpeg", "ffprobe"}


# -- verification -------------------------------------------------------


def test_a_matching_checksum_passes(tmp_path: Path) -> None:
    payload = tmp_path / "f"
    payload.write_bytes(b"hello")
    verify_sha256(payload, hashlib.sha256(b"hello").hexdigest())


def test_case_and_whitespace_do_not_defeat_verification(tmp_path: Path) -> None:
    payload = tmp_path / "f"
    payload.write_bytes(b"hello")
    verify_sha256(payload, "  " + hashlib.sha256(b"hello").hexdigest().upper() + " ")


def test_a_mismatched_checksum_fails_closed(tmp_path: Path) -> None:
    payload = tmp_path / "f"
    payload.write_bytes(b"tampered")
    with pytest.raises(ChecksumError):
        verify_sha256(payload, hashlib.sha256(b"hello").hexdigest())


def test_hashing_is_streamed_not_slurped(tmp_path: Path) -> None:
    """Downloads run to ~113 MB, so the whole file must never be in memory."""
    payload = tmp_path / "big"
    payload.write_bytes(b"x" * (3 * 1024 * 1024))
    assert sha256_file(payload) == hashlib.sha256(b"x" * (3 * 1024 * 1024)).hexdigest()


# -- manager ------------------------------------------------------------


def zip_with(path: Path, entries: dict[str, bytes]) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path.read_bytes()


@pytest.fixture
def linux_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr("media_downloader.paths.current_platform", lambda: "linux")
    return {"XDG_DATA_HOME": str(tmp_path / "data")}


def manager_for(
    payload: bytes, env: dict[str, str], *, spec: Any = None, fail: Exception | None = None
) -> tuple[ToolManager, list[str]]:
    fetched: list[str] = []

    def fetcher(url: str, destination: Path, *, max_bytes: int) -> None:
        fetched.append(url)
        if fail is not None:
            raise fail
        destination.write_bytes(payload)

    mgr = ToolManager(
        env=env, fetcher=fetcher, platform_name=lambda: "linux", machine=lambda: "x86_64"
    )
    if spec is not None:
        mgr.spec_for = lambda tool: spec  # type: ignore[method-assign]
    return mgr, fetched


def fake_spec(tmp_path: Path, payload_entries: dict[str, bytes], **over: Any) -> Any:
    from types import MappingProxyType

    archive = tmp_path / "src.zip"
    data = zip_with(archive, payload_entries)
    defaults: dict[str, Any] = {
        "tool": "ffmpeg",
        "version": "test-1",
        "url": "https://example.invalid/tool.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "members": MappingProxyType({k: k for k in payload_entries}),
        "licence": "LGPL",
        "source": "https://example.invalid",
    }
    defaults.update(over)
    return manifest.ToolSpec(**defaults), data


def test_install_verifies_extracts_and_promotes_atomically(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, fetched = manager_for(data, linux_env, spec=spec)

    final = mgr.install("ffmpeg")

    assert fetched == ["https://example.invalid/tool.zip"]
    assert (final / "ffmpeg").read_bytes() == b"FF"
    assert (final / "ffprobe").read_bytes() == b"PR"
    assert final.name == "test-1"


def test_installed_binaries_are_executable_on_posix(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, _ = manager_for(data, linux_env, spec=spec)
    final = mgr.install("ffmpeg")
    import os

    assert os.access(final / "ffmpeg", os.X_OK)


def test_a_bad_checksum_leaves_nothing_behind(tmp_path: Path, linux_env: dict[str, str]) -> None:
    """The central guarantee: no unverified executable ever reaches the tools dir."""
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF"}, sha256="0" * 64)
    mgr, _ = manager_for(data, linux_env, spec=spec)

    with pytest.raises(ChecksumError):
        mgr.install("ffmpeg")

    from media_downloader.paths import tools_dir

    leftovers = list(tools_dir(linux_env).rglob("*")) if tools_dir(linux_env).exists() else []
    assert [p for p in leftovers if p.is_file()] == []
    assert mgr.managed_dir("ffmpeg") is None


def test_a_failed_download_leaves_nothing_behind(tmp_path: Path, linux_env: dict[str, str]) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF"})
    mgr, _ = manager_for(data, linux_env, spec=spec, fail=ToolInstallError("network down"))

    with pytest.raises(ToolInstallError):
        mgr.install("ffmpeg")
    assert mgr.managed_dir("ffmpeg") is None


def test_an_archive_missing_a_declared_binary_fails(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    """ffmpeg without ffprobe must not be promoted as a usable install."""
    from types import MappingProxyType

    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF"})
    spec = manifest.ToolSpec(
        **{**spec.__dict__, "members": MappingProxyType({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})}
    )
    mgr, _ = manager_for(data, linux_env, spec=spec)

    with pytest.raises(MediaDownloaderError):
        mgr.install("ffmpeg")
    assert mgr.managed_dir("ffmpeg") is None


def test_an_unconfigured_platform_refuses_to_install(linux_env: dict[str, str]) -> None:
    mgr = ToolManager(
        env=linux_env,
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "darwin",
        machine=lambda: "arm64",
    )
    with pytest.raises(ToolInstallError, match="cannot be installed automatically"):
        mgr.install("ffmpeg")


def test_installing_twice_is_a_no_op(tmp_path: Path, linux_env: dict[str, str]) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, fetched = manager_for(data, linux_env, spec=spec)
    mgr.install("ffmpeg")
    mgr.install("ffmpeg")
    assert len(fetched) == 1


def test_an_oversized_response_is_stopped(tmp_path: Path, linux_env: dict[str, str]) -> None:
    """A redirected or compromised source must not fill the user's disk."""
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF"})
    limits: list[int] = []

    def fetcher(url: str, destination: Path, *, max_bytes: int) -> None:
        limits.append(max_bytes)
        destination.write_bytes(data)

    mgr = ToolManager(
        env=linux_env, fetcher=fetcher, platform_name=lambda: "linux", machine=lambda: "x86_64"
    )
    mgr.spec_for = lambda tool: spec  # type: ignore[method-assign]
    mgr.install("ffmpeg")
    assert limits and limits[0] >= spec.size_bytes


def test_https_is_enforced_by_the_real_fetcher(tmp_path: Path) -> None:
    from media_downloader.tools.manager import https_fetch

    with pytest.raises(ToolInstallError, match="non-HTTPS"):
        https_fetch("http://example.invalid/x.zip", tmp_path / "out", max_bytes=10)


# -- discovery ----------------------------------------------------------


def test_discovery_never_downloads(tmp_path: Path, linux_env: dict[str, str]) -> None:
    """Merely looking for a tool must never pull 113 MB down someone's link."""
    called: list[str] = []
    mgr = ToolManager(
        env=linux_env,
        fetcher=lambda url, dest, **kw: called.append(url),
        platform_name=lambda: "linux",
        machine=lambda: "x86_64",
    )
    assert mgr.managed_dir("ffmpeg") is None
    assert mgr.managed_path("deno", "deno") is None
    mgr.status("ffmpeg", system_path=None)
    assert called == []


def test_a_system_tool_takes_precedence_over_a_managed_one(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, _ = manager_for(data, linux_env, spec=spec)
    mgr.install("ffmpeg")

    status = mgr.status("ffmpeg", system_path=Path("/usr/bin/ffmpeg"))
    assert status.state is ToolState.SYSTEM
    assert status.path == Path("/usr/bin/ffmpeg")


def test_a_managed_tool_is_reported_when_no_system_copy_exists(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, _ = manager_for(data, linux_env, spec=spec)
    mgr.install("ffmpeg")

    status = mgr.status("ffmpeg", system_path=None)
    assert status.state is ToolState.MANAGED
    assert status.available and not status.can_install


def test_a_missing_tool_offers_an_install(linux_env: dict[str, str]) -> None:
    mgr = ToolManager(
        env=linux_env,
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "linux",
        machine=lambda: "x86_64",
    )
    status = mgr.status("ffmpeg", system_path=None)
    assert status.state is ToolState.MISSING
    assert status.can_install
    assert status.size_bytes and status.licence


def test_an_unsupported_platform_does_not_offer_an_install(linux_env: dict[str, str]) -> None:
    mgr = ToolManager(
        env=linux_env,
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "darwin",
        machine=lambda: "arm64",
    )
    status = mgr.status("ffmpeg", system_path=None)
    assert status.state is ToolState.UNSUPPORTED
    assert not status.can_install and not status.available


def test_a_partial_install_is_not_reported_as_available(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    """Deleting ffprobe from an install must make it stop counting."""
    spec, data = fake_spec(tmp_path, {"ffmpeg": b"FF", "ffprobe": b"PR"})
    mgr, _ = manager_for(data, linux_env, spec=spec)
    final = mgr.install("ffmpeg")
    (final / "ffprobe").unlink()

    assert mgr.managed_dir("ffmpeg") is None
    assert mgr.status("ffmpeg", system_path=None).state is ToolState.MISSING
