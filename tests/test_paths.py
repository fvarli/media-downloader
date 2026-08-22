"""Per-user application directories on all three platforms."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader import paths


@pytest.fixture(autouse=True)
def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _on(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(paths, "current_platform", lambda: platform)


def test_macos_uses_application_support(_fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, "darwin")
    assert paths.app_data_dir({}) == (
        _fake_home / "Library" / "Application Support" / "Media Downloader"
    )


def test_windows_uses_localappdata(_fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, "win32")
    local = _fake_home / "AppData" / "Local"
    assert paths.app_data_dir({"LOCALAPPDATA": str(local)}) == local / "Media Downloader"


def test_windows_falls_back_when_localappdata_is_unset(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stripped environment must not make the path resolution fail."""
    _on(monkeypatch, "win32")
    expected = _fake_home / "AppData" / "Local" / "Media Downloader"
    assert paths.app_data_dir({}) == expected
    assert paths.app_data_dir({"LOCALAPPDATA": "   "}) == expected


def test_linux_honours_xdg_data_home(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _on(monkeypatch, "linux")
    xdg = tmp_path / "xdg"
    assert paths.app_data_dir({"XDG_DATA_HOME": str(xdg)}) == xdg / "media-downloader"


def test_linux_falls_back_to_local_share(_fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, "linux")
    expected = _fake_home / ".local" / "share" / "media-downloader"
    assert paths.app_data_dir({}) == expected


def test_a_relative_xdg_data_home_is_ignored(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The XDG spec says a relative value is invalid; do not use it."""
    _on(monkeypatch, "linux")
    expected = _fake_home / ".local" / "share" / "media-downloader"
    assert paths.app_data_dir({"XDG_DATA_HOME": "relative/path"}) == expected


def test_an_unknown_platform_uses_the_linux_convention(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, "freebsd14")
    assert paths.app_data_dir({}).name == "media-downloader"


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_every_resolved_path_is_absolute(
    platform: str, _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, platform)
    assert paths.app_data_dir({}).is_absolute()
    assert paths.tools_dir({}).is_absolute()


def test_tools_and_install_directories_nest_under_app_data(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, "linux")
    root = paths.app_data_dir({})
    assert paths.tools_dir({}) == root / "tools"
    assert paths.tool_install_dir("ffmpeg", "n9.0.1", {}) == root / "tools" / "ffmpeg" / "n9.0.1"


def test_resolution_never_creates_anything(
    _fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importing or querying must not leave directories on someone's disk."""
    _on(monkeypatch, "linux")
    for resolved in (
        paths.app_data_dir({}),
        paths.tools_dir({}),
        paths.tool_install_dir("ffmpeg", "1", {}),
    ):
        assert not resolved.exists()


def test_resolution_is_deterministic(_fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, "linux")
    assert paths.app_data_dir({}) == paths.app_data_dir({})


def test_ensure_dir_creates_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert paths.ensure_dir(target) == target
    assert target.is_dir()
    paths.ensure_dir(target)


def test_ensure_dir_reports_a_usable_error(tmp_path: Path) -> None:
    from media_downloader.errors import OutputError

    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OutputError):
        paths.ensure_dir(blocker / "child")
