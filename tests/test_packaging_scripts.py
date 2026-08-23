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


# -- the licensing gate --------------------------------------------------

LGPL_BUILDCONF = """configuration:
  --prefix=/tmp/install
  --disable-autodetect
  --enable-libmp3lame
  --enable-libopus
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


@pytest.mark.parametrize("missing", ["--enable-libmp3lame", "--enable-libopus"])
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
