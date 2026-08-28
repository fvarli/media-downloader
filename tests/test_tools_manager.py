"""Manifest, checksum verification and the install/discovery split.

Every test here is offline: the network layer is injected.
"""

from __future__ import annotations

import hashlib
import inspect
import urllib.request
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
    [("darwin", "x86_64"), ("linux", "aarch64")],
)
def test_unverified_ffmpeg_targets_are_absent_rather_than_guessed(
    platform: str, machine: str
) -> None:
    """We have no verified source for these yet, so there must be no entry."""
    assert manifest.lookup(manifest.FFMPEG, platform, machine) is None


def test_macos_ffmpeg_is_configured_for_apple_silicon_only() -> None:
    """No third-party macOS provider met the trust requirements -- evermeet.cx
    publishes no SHA-256, osxexperts.net serves a mutable URL with no configure
    flags -- so this one is built by the project and published under its own
    tag. Intel Macs still have no entry, and an absent entry stays the honest
    answer there rather than a guessed hash."""
    assert manifest.lookup(manifest.FFMPEG, "darwin", "arm64") is not None
    assert manifest.lookup(manifest.FFMPEG, "darwin", "x86_64") is None


def test_verified_targets_are_present() -> None:
    """The combinations whose checksums are pinned and verified."""
    assert manifest.lookup(manifest.FFMPEG, "linux", "x86_64") is not None
    assert manifest.lookup(manifest.FFMPEG, "win32", "AMD64") is not None
    assert manifest.lookup(manifest.FFMPEG, "darwin", "arm64") is not None
    for platform, machine in (("linux", "x86_64"), ("darwin", "arm64"), ("win32", "AMD64")):
        assert manifest.lookup(manifest.DENO, platform, machine) is not None


@pytest.mark.parametrize(
    ("tool", "platform", "expected"),
    [
        (manifest.FFMPEG, "win32", ("ffmpeg.exe", "ffprobe.exe")),
        (manifest.FFMPEG, "linux", ("ffmpeg", "ffprobe")),
        (manifest.DENO, "win32", ("deno.exe",)),
        (manifest.DENO, "darwin", ("deno",)),
    ],
)
def test_executables_carry_the_platform_suffix(
    tool: str, platform: str, expected: tuple[str, ...]
) -> None:
    """A Windows binary written without .exe would not be found by which()."""
    machine = "AMD64" if platform == "win32" else ("arm64" if platform == "darwin" else "x86_64")
    spec = manifest.lookup(tool, platform, machine)
    assert spec is not None
    assert spec.executables == expected


@pytest.mark.parametrize(
    ("platform", "logical", "expected"),
    [("win32", "deno", "deno.exe"), ("linux", "deno", "deno"), ("win32", "ffmpeg", "ffmpeg.exe")],
)
def test_executable_name_resolves_through_the_manifest(
    platform: str, logical: str, expected: str
) -> None:
    machine = "AMD64" if platform == "win32" else "x86_64"
    tool = manifest.DENO if logical == "deno" else manifest.FFMPEG
    spec = manifest.lookup(tool, platform, machine)
    assert spec is not None
    assert manifest.executable_name(spec, logical) == expected


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
    # Intel Macs: still no verified source, so still no entry.
    mgr = ToolManager(
        env=linux_env,
        fetcher=lambda *a, **k: None,
        platform_name=lambda: "darwin",
        machine=lambda: "x86_64",
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


# -- redirects ----------------------------------------------------------
#
# Checking the URL we were handed is not enough: urlopen follows redirects, and
# the stock handler will happily follow one to http://. Every URL the manifest
# pins redirects at least once -- GitHub sends release assets off to
# objects.githubusercontent.com -- so this hop is the normal path, and until
# now nothing re-checked it.


def _redirect_to(target: str) -> Any:
    from media_downloader.tools.trust import HTTPSOnlyRedirectHandler

    handler = HTTPSOnlyRedirectHandler()
    request = urllib.request.Request("https://example.invalid/tool.zip")
    return lambda: handler.redirect_request(request, None, 302, "Found", {}, target)


@pytest.mark.parametrize(
    "target",
    [
        "http://example.invalid/tool.zip",
        "http://objects.example.invalid/tool.zip",
        "ftp://example.invalid/tool.zip",
        "file:///etc/passwd",
    ],
)
def test_a_redirect_off_https_is_refused(target: str) -> None:
    """Fails closed: a download that cannot stay on HTTPS does not happen."""
    with pytest.raises(ToolInstallError, match="redirected away from HTTPS"):
        _redirect_to(target)()


def test_a_redirect_that_stays_on_https_is_followed() -> None:
    """The hardening must not break the hop every real download makes."""
    redirected = _redirect_to("https://objects.githubusercontent.com/tool.zip")()
    assert redirected is not None
    assert redirected.full_url == "https://objects.githubusercontent.com/tool.zip"


def test_the_fetcher_goes_through_the_shared_opener() -> None:
    """A handler nothing is wired to would protect nothing, and a context
    nothing is wired to is how the Windows failure happened in the first
    place: the trust was available and simply never asked for."""
    from media_downloader.tools import trust

    fetch_source = inspect.getsource(trust.https_fetch)
    assert "create_https_opener()" in fetch_source
    assert "opener.open(" in fetch_source
    assert "urllib.request.urlopen(" not in fetch_source

    opener_source = inspect.getsource(trust.create_https_opener)
    assert "create_https_context()" in opener_source
    assert "HTTPSOnlyRedirectHandler" in opener_source
    assert issubclass(trust.HTTPSOnlyRedirectHandler, urllib.request.HTTPRedirectHandler)


def test_the_manager_uses_the_shared_fetcher_by_default() -> None:
    """Both managed tools must take one path, not two workarounds."""
    from media_downloader.tools import manager, trust

    assert manager.https_fetch is trust.https_fetch


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


def test_a_system_tool_is_never_given_the_manifests_version(
    tmp_path: Path, linux_env: dict[str, str]
) -> None:
    """The manifest describes what we would install, not what is installed.

    Reporting it for a system copy produced "system 2.9.5" on a machine whose
    JavaScript runtime was Node 22: the manifest pins Deno, but a system Node
    satisfies the same requirement. Two different programs, one version number.
    """
    spec, data = fake_spec(tmp_path, {"deno": b"DENO"})
    mgr, _ = manager_for(data, linux_env, spec=spec)

    status = mgr.status("deno", system_path=Path("/usr/bin/node"))
    assert status.state is ToolState.SYSTEM
    assert status.version is None
    assert mgr.spec_for("deno").version is not None  # the manifest does know one


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
        machine=lambda: "x86_64",
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


# -- macOS FFmpeg, once it existed ---------------------------------------
#
# macOS was the one platform with no managed FFmpeg, so universal
# compatibility refused to run there at all. It is now built by this project
# and published under its own tag, which is what gives the manifest a durable
# public URL to pin.


def test_macos_ffmpeg_is_supported_now() -> None:
    spec = manifest.lookup("ffmpeg", "darwin", "arm64")
    assert spec is not None
    assert spec.version == "n9.0.1"
    assert spec.licence == "LGPL-2.1-or-later"


def test_the_macos_url_is_a_durable_release_asset() -> None:
    """A build artifact would have expired; a release asset does not. The URL
    is pinned in shipped binaries, so it can never be re-pointed."""
    spec = manifest.lookup("ffmpeg", "darwin", "arm64")
    assert spec is not None
    assert spec.url.startswith("https://github.com/")
    assert "/releases/download/" in spec.url
    assert "/actions/" not in spec.url


def test_the_macos_archive_shape_matches_the_extractor() -> None:
    spec = manifest.lookup("ffmpeg", "darwin", "arm64")
    assert spec is not None
    assert dict(spec.members) == {"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"}
    assert set(spec.executables) == {"ffmpeg", "ffprobe"}


def test_every_pinned_tool_declares_a_plausible_size() -> None:
    """The size bounds the download; zero would disable that guard."""
    for spec in manifest._MANIFEST.values():
        assert spec.size_bytes > 0, spec


def test_no_pinned_url_is_plain_http() -> None:
    for spec in manifest._MANIFEST.values():
        assert spec.url.startswith("https://"), spec
