"""How the application knows it was built without a console.

This is a build-time fact, so it is recorded at build time. Guessing was
tempting and would have been wrong: sys.stdout is None for a windowed Windows
build but not for a macOS .app launched from Finder, and "is stdout a
terminal?" answers a different question than "was this built windowed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader import buildmode


@pytest.fixture
def _marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the resource lookup at a marker we control."""

    class FakeTraversable:
        def __init__(self, path: Path) -> None:
            self._path = path

        def joinpath(self, name: str) -> FakeTraversable:
            return FakeTraversable(self._path / name)

        def read_text(self, encoding: str = "utf-8") -> str:
            return self._path.read_text(encoding=encoding)

    monkeypatch.setattr(buildmode, "files", lambda package: FakeTraversable(tmp_path))
    return tmp_path / buildmode.MARKER_NAME


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        ("windowed", buildmode.WINDOWED),
        ("windowed\n", buildmode.WINDOWED),
        ("  WINDOWED  ", buildmode.WINDOWED),
        ("console", buildmode.CONSOLE),
        ("", buildmode.CONSOLE),
        ("something else", buildmode.CONSOLE),
    ],
)
def test_the_recorded_mode_is_read_back(_marker: Path, recorded: str, expected: str) -> None:
    _marker.write_text(recorded, encoding="utf-8")
    assert buildmode.build_mode() == expected


def test_a_missing_marker_falls_back_to_console(_marker: Path) -> None:
    """Running from source, or an older bundle. Console is the safe default:
    the worst it costs is a usage message somebody can actually see."""
    assert not _marker.exists()
    assert buildmode.build_mode() == buildmode.CONSOLE


def test_a_source_checkout_is_never_windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buildmode, "build_mode", lambda: buildmode.WINDOWED)
    monkeypatch.setattr(buildmode, "is_frozen", lambda: False)
    assert buildmode.is_windowed_app() is False


def test_a_frozen_console_build_is_not_windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """It still has somewhere to print, so the CLI contract holds there."""
    monkeypatch.setattr(buildmode, "build_mode", lambda: buildmode.CONSOLE)
    monkeypatch.setattr(buildmode, "is_frozen", lambda: True)
    assert buildmode.is_windowed_app() is False


def test_a_frozen_windowed_build_is_the_only_case_that_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(buildmode, "build_mode", lambda: buildmode.WINDOWED)
    monkeypatch.setattr(buildmode, "is_frozen", lambda: True)
    assert buildmode.is_windowed_app() is True


def test_an_unreadable_marker_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup must survive a resource lookup that fails for any reason."""

    def explode(package: str) -> Any:
        raise OSError("no such bundle")

    monkeypatch.setattr(buildmode, "files", explode)
    assert buildmode.build_mode() == buildmode.CONSOLE
