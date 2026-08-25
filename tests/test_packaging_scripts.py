"""Offline tests for the packaging scripts.

These scripts run on CI runners and on the maintainer's machine, where a wrong
answer is expensive to notice: a build that quietly ships GPL components, or a
smoke test that talks to whatever else happened to hold a port. The logic that
decides those things is pure text handling, so it can be judged here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_archive = _load(PACKAGING / "ffmpeg" / "verify_archive.py", "_verify_archive")
smoke_test = _load(PACKAGING / "smoke_test.py", "_smoke_test")
verify_package = _load(PACKAGING / "verify_package.py", "_verify_package")


# -- the licensing gate --------------------------------------------------

LGPL_BUILDCONF = """configuration:
  --prefix=/tmp/install
  --disable-autodetect
  --enable-libmp3lame
  --enable-libopus
  --enable-libopenh264
  --enable-zlib
  --disable-shared
"""


def test_a_clean_lgpl_build_passes_every_criterion() -> None:
    assert all(verify_archive.licensing_verdict(LGPL_BUILDCONF).values())


@pytest.mark.parametrize("flag", ["--enable-gpl", "--enable-nonfree"])
def test_a_gpl_or_nonfree_build_is_rejected(flag: str) -> None:
    """The whole point of building it ourselves is knowing this cannot slip in."""
    verdict = verify_archive.licensing_verdict(LGPL_BUILDCONF + f"  {flag}\n")
    assert verdict[flag] is False
    assert not all(verdict.values())


@pytest.mark.parametrize(
    "missing", ["--enable-libmp3lame", "--enable-libopus", "--enable-libopenh264"]
)
def test_a_build_missing_an_encoder_is_rejected(missing: str) -> None:
    """FFmpeg has no native MP3 encoder and is never asked for its native Opus
    one, so losing either library silently breaks a format the interface
    offers. Silently is the problem; this makes it loud."""
    verdict = verify_archive.licensing_verdict(LGPL_BUILDCONF.replace(f"  {missing}\n", ""))
    assert verdict[missing] is False


def test_enable_gpl_is_not_confused_with_a_longer_flag() -> None:
    """--enable-gpl must not be read out of --enable-gpl-something-else."""
    verdict = verify_archive.licensing_verdict(LGPL_BUILDCONF + "  --enable-gplv3\n")
    assert verdict["--enable-gpl"] is False  # a superstring is still a match


# -- smoke-test port discovery -------------------------------------------

LOG = """2026-08-21 10:00:00,000 INFO media_downloader.launcher: startup version=0.2.0
2026-08-21 10:00:00,100 INFO media_downloader.launcher: environment ffmpeg=available
2026-08-21 10:00:00,200 INFO media_downloader.launcher: server listening on http://127.0.0.1:8765
"""


def test_the_address_is_read_from_the_log_not_guessed() -> None:
    assert smoke_test.LISTENING.findall(LOG) == ["http://127.0.0.1:8765"]


def test_an_ephemeral_port_is_read_just_as_well() -> None:
    """The application falls back to an ephemeral port when 8765 is taken, so
    assuming the preferred one tests whatever else is listening there."""
    log = LOG.replace("8765", "49213")
    assert smoke_test.LISTENING.findall(log) == ["http://127.0.0.1:49213"]


def test_a_log_with_no_bind_record_yields_nothing() -> None:
    assert smoke_test.LISTENING.findall(LOG.rsplit("\n", 2)[0]) == []


def test_only_loopback_addresses_are_recognised() -> None:
    """A bind to anything but loopback must not be silently accepted."""
    log = LOG.replace("127.0.0.1", "0.0.0.0")
    assert smoke_test.LISTENING.findall(log) == []


def test_the_last_record_wins_when_a_run_rebinds() -> None:
    log = LOG + LOG.splitlines()[-1].replace("8765", "9001") + "\n"
    assert smoke_test.LISTENING.findall(log)[-1] == "http://127.0.0.1:9001"


def test_an_error_id_can_be_recovered_for_a_failure_report() -> None:
    log = (
        LOG
        + "2026-08-21 10:00:01,000 ERROR startup failed error_id=MD-20260821-H7K2QP type=OSError\n"
    )
    assert smoke_test.ERROR_ID.findall(log) == ["MD-20260821-H7K2QP"]


# -- isolation of the scripts themselves ---------------------------------


def test_every_platforms_app_data_rule_is_redirected() -> None:
    """Overriding only XDG_DATA_HOME and LOCALAPPDATA left macOS resolving the
    runner's real application-support directory, by way of HOME."""
    env = smoke_test.isolated(Path("/somewhere"))
    assert set(env) == {"XDG_DATA_HOME", "LOCALAPPDATA", "HOME", "USERPROFILE"}
    assert set(env.values()) == {str(Path("/somewhere"))}


# -- the distributable archive -------------------------------------------
#
# GitHub's artifact upload documents that it does not preserve permissions:
# everything arrives as 644. A macOS .app delivered that way cannot launch at
# all, so we build the archive ourselves and these are the judgements that say
# whether doing so worked.


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_an_archive_holding_exactly_the_payload_is_accepted(platform: str) -> None:
    root = verify_package.EXPECTED_ROOT[platform]
    assert verify_package.layout_problems(platform, [root]) == []


def test_the_duplicate_collect_directory_beside_the_app_is_rejected() -> None:
    """The specific thing that shipped before.

    A windowed macOS build leaves both the .app and PyInstaller's COLLECT
    directory in dist/, and uploading dist/ wholesale sent both: 429 files
    where the bundle itself is 129.
    """
    problems = verify_package.layout_problems("macos", ["Media Downloader.app", "media-downloader"])
    assert problems
    assert any("media-downloader" in p for p in problems)


@pytest.mark.parametrize("leftover", ["build", "artifact-info.json", "__pycache__"])
def test_build_leftovers_are_named_not_merely_counted(leftover: str) -> None:
    problems = verify_package.layout_problems("linux", ["media-downloader", leftover])
    assert any(leftover in p for p in problems)


def test_an_empty_archive_is_rejected() -> None:
    assert verify_package.layout_problems("linux", [])


@pytest.mark.parametrize("mode", [0o755, 0o700, 0o555, 0o111])
def test_a_surviving_execute_bit_is_recognised(mode: int) -> None:
    assert verify_package.is_executable(mode) is True


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o444, 0o600])
def test_the_permissions_github_would_have_left_are_rejected(mode: int) -> None:
    """0o644 is exactly what upload-artifact documents it produces."""
    assert verify_package.is_executable(mode) is False


def test_windows_is_deliberately_exempt_from_the_permission_check() -> None:
    """Executability on Windows is not carried by a permission bit, so
    demanding one there would be superstition rather than a check."""
    assert verify_package.MUST_BE_EXECUTABLE["windows"] is None
    assert verify_package.MUST_BE_EXECUTABLE["macos"] is not None
    assert verify_package.MUST_BE_EXECUTABLE["linux"] is not None


def test_every_platform_is_described_consistently() -> None:
    platforms = set(verify_package.EXPECTED_ROOT)
    assert platforms == set(verify_package.REQUIRED_PATHS) == set(verify_package.MUST_BE_EXECUTABLE)


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_the_executable_checked_is_one_of_the_required_paths(platform: str) -> None:
    """Otherwise a rename could leave the permission check pointing at nothing
    and passing by looking at an absent file."""
    relative = verify_package.MUST_BE_EXECUTABLE[platform]
    if relative is not None:
        assert relative in verify_package.REQUIRED_PATHS[platform]


# -- support-report hygiene ----------------------------------------------


def test_the_report_denylist_covers_every_named_leak() -> None:
    """The list is the check; a quiet removal from it removes a guarantee."""
    forbidden = {item.lower() for item in smoke_test.FORBIDDEN_IN_REPORT}
    for required in ("x-md-token", "authorization", "cookie", "pytest", "/runner/"):
        assert required in forbidden


# -- the frozen entry point's failure reporting --------------------------
#
# A windowed build that exits without opening anything must say so, because
# there is no console for it to have said anything in. The trigger has to be
# narrow: an earlier version fired on *any* non-zero exit, so passing a bad URL
# to a windowed build popped a modal dialog. On an unattended machine nobody
# dismisses it, and it hung two CI jobs for two minutes each.


entry = _load(PACKAGING / "entry.py", "_entry")


@pytest.fixture
def _windowed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A windowed build whose dialog records instead of blocking."""
    shown: list[str] = []
    import media_downloader.buildmode as buildmode_module
    import media_downloader.web.system as system_module

    monkeypatch.setattr(buildmode_module, "is_windowed_app", lambda: True)
    monkeypatch.setattr(
        system_module,
        "show_startup_error",
        lambda message, error_id, log_dir=None: shown.append(message),
    )
    return shown


def test_a_silent_double_click_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, _windowed: list[str]
) -> None:
    monkeypatch.setattr(entry.sys, "argv", ["media-downloader"])
    entry._report_invisible_failure(None, 2)
    assert _windowed


def test_a_failure_with_arguments_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, _windowed: list[str]
) -> None:
    """The bad-URL case: a command line was used, so output has a home."""
    monkeypatch.setattr(entry.sys, "argv", ["media-downloader", "not-a-url"])
    entry._report_invisible_failure(None, 3)
    assert not _windowed


def test_a_console_build_never_pops_a_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    import media_downloader.buildmode as buildmode_module
    import media_downloader.web.system as system_module

    monkeypatch.setattr(buildmode_module, "is_windowed_app", lambda: False)
    monkeypatch.setattr(system_module, "show_startup_error", lambda *a, **k: shown.append("x"))
    monkeypatch.setattr(entry.sys, "argv", ["media-downloader"])
    entry._report_invisible_failure(None, 2)
    assert not shown


def test_reporting_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A build that is already failing must not fail differently."""
    import media_downloader.buildmode as buildmode_module

    def explode() -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(buildmode_module, "is_windowed_app", explode)
    monkeypatch.setattr(entry.sys, "argv", ["media-downloader"])
    entry._report_invisible_failure(RuntimeError("original"), 1)  # must not raise


def test_a_build_with_no_h264_encoder_is_rejected() -> None:
    """The exact shape of the first macOS build.

    It had no H.264 encoder at all -- libx264 is GPL and impossible under our
    licensing, and nothing was put in its place -- so every universal
    compatibility conversion on macOS would have failed on the encoder. It
    passed every gate that existed at the time, because none of them asked.
    """
    without = LGPL_BUILDCONF.replace("  --enable-libopenh264\n", "")
    verdict = verify_archive.licensing_verdict(without)
    assert verdict["--enable-libopenh264"] is False
    assert not all(verdict.values())


def test_the_gpl_encoder_is_never_an_acceptable_substitute() -> None:
    """libx264 would satisfy "has an H.264 encoder" and break the licence."""
    with_x264 = LGPL_BUILDCONF.replace(
        "  --enable-libopenh264\n", "  --enable-libx264\n  --enable-gpl\n"
    )
    verdict = verify_archive.licensing_verdict(with_x264)
    assert verdict["--enable-gpl"] is False
    assert verdict["--enable-libopenh264"] is False
