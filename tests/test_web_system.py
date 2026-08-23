"""OS integration: download directory, file manager, browser. No real launches."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from media_downloader.errors import OutputError
from media_downloader.web import system


def test_prefers_a_dedicated_folder_inside_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr(system.Path, "home", classmethod(lambda cls: tmp_path))
    assert system.default_download_dir() == tmp_path / "Downloads" / system.APP_FOLDER_NAME


def test_falls_back_to_home_without_a_downloads_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system.Path, "home", classmethod(lambda cls: tmp_path))
    assert system.default_download_dir() == tmp_path / system.APP_FOLDER_NAME


def test_falls_back_to_the_working_directory_without_a_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_home(cls: Any) -> Path:
        raise RuntimeError("no home directory")

    monkeypatch.setattr(system.Path, "home", classmethod(no_home))
    assert system.default_download_dir().name == "downloads"


def test_the_default_directory_is_never_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr(system.Path, "home", classmethod(lambda cls: tmp_path))
    assert system.default_download_dir().is_absolute()


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "open"), ("linux", "xdg-open"), ("freebsd", "xdg-open")],
)
def test_open_folder_uses_the_right_command_per_platform(
    platform: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        calls.append(command)
        assert "shell" not in kwargs, "a shell must never be involved"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(system, "current_platform", lambda: platform)
    monkeypatch.setattr(system.subprocess, "run", fake_run)
    system.open_folder(tmp_path)

    assert calls == [[expected, str(tmp_path.resolve())]]


def test_open_folder_passes_an_argument_list_not_a_shell_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path with spaces or shell metacharacters must stay one argument."""
    awkward = tmp_path / "My Folder; rm -rf ~"
    awkward.mkdir()
    captured: list[Any] = []

    monkeypatch.setattr(system, "current_platform", lambda: "linux")
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda command, **kw: (
            captured.append((command, kw)) or subprocess.CompletedProcess(command, 0)
        ),
    )
    system.open_folder(awkward)

    command, kwargs = captured[0]
    assert isinstance(command, list) and len(command) == 2
    assert command[1] == str(awkward.resolve())
    assert kwargs.get("shell") is not True


def test_open_folder_uses_startfile_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(system, "current_platform", lambda: "win32")
    monkeypatch.setattr(system.os, "startfile", seen.append, raising=False)
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda *a, **k: pytest.fail("Windows must not shell out"),
    )
    system.open_folder(tmp_path)
    assert seen == [tmp_path.resolve()]


def test_open_folder_refuses_a_path_that_is_not_a_directory(tmp_path: Path) -> None:
    afile = tmp_path / "file.txt"
    afile.write_text("x")
    with pytest.raises(OutputError):
        system.open_folder(afile)
    with pytest.raises(OutputError):
        system.open_folder(tmp_path / "missing")


def test_a_missing_file_manager_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system, "current_platform", lambda: "linux")
    monkeypatch.setattr(
        system.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )
    with pytest.raises(OutputError) as excinfo:
        system.open_folder(tmp_path)
    assert str(tmp_path.resolve()) in (excinfo.value.hint or "")


def test_open_browser_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system.webbrowser, "open", lambda url: True)
    assert system.open_browser("http://127.0.0.1:8765") is True


def test_a_machine_with_no_browser_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headless, SSH and WSL are legitimate; the caller prints the URL instead."""
    monkeypatch.setattr(system.webbrowser, "open", lambda url: False)
    assert system.open_browser("http://127.0.0.1:8765") is False

    monkeypatch.setattr(
        system.webbrowser, "open", lambda url: (_ for _ in ()).throw(RuntimeError("no display"))
    )
    assert system.open_browser("http://127.0.0.1:8765") is False


def test_current_platform_reports_the_real_platform() -> None:
    """The seam must default to the truth, not to a fixed value."""
    import sys

    assert system.current_platform() == sys.platform


# --- startup-address fallback ------------------------------------------
#
# A packaged double-clickable app has no console, so a printed URL would be
# invisible if the browser could not be opened.


def test_windows_shows_a_message_box(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    class FakeUser32:
        @staticmethod
        def MessageBoxW(*args: Any) -> int:  # noqa: N802 - Win32 naming
            calls.append(args)
            return 1

    monkeypatch.setattr(system, "current_platform", lambda: "win32")
    monkeypatch.setitem(
        __import__("sys").modules,
        "ctypes",
        type("C", (), {"windll": type("W", (), {"user32": FakeUser32})}),
    )
    system.report_startup_url("http://127.0.0.1:8765")
    assert calls and "http://127.0.0.1:8765" in calls[0][1]


def test_macos_uses_osascript_with_an_argument_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(system, "current_platform", lambda: "darwin")
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda command, **kw: captured.append((command, kw)) or None,
    )
    system.report_startup_url("http://127.0.0.1:8765")

    command, kwargs = captured[0]
    assert isinstance(command, list)
    assert command[0] == "osascript"
    assert "http://127.0.0.1:8765" in command[-1]
    assert kwargs.get("shell") is not True


def test_linux_falls_back_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(system, "current_platform", lambda: "linux")
    system.report_startup_url("http://127.0.0.1:8765")
    assert "http://127.0.0.1:8765" in capsys.readouterr().err


def test_a_failing_dialog_is_never_fatal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server is already running; a dialog failure must not stop it."""
    monkeypatch.setattr(system, "current_platform", lambda: "darwin")
    monkeypatch.setattr(
        system.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no osascript"))
    )
    system.report_startup_url("http://127.0.0.1:8765")
    assert "http://127.0.0.1:8765" in capsys.readouterr().err


def test_applescript_quoting_escapes_dangerous_characters() -> None:
    quoted = system._applescript_string('say "hi" \\ then')
    assert quoted.startswith('"') and quoted.endswith('"')
    assert '\\"' in quoted


# --- startup-error dialog ----------------------------------------------


def test_startup_error_carries_the_id_and_log_location(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(system, "current_platform", lambda: "linux")
    system.show_startup_error("Port 8765 is in use.", "MD-20260823-A1B2C3", Path("/logs"))

    err = capsys.readouterr().err
    assert "could not start" in err
    assert "MD-20260823-A1B2C3" in err
    assert "/logs" in err


def test_startup_error_shows_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stack trace helps nobody who cannot read one."""
    monkeypatch.setattr(system, "current_platform", lambda: "linux")
    system.show_startup_error("Something failed.", "MD-1", None)
    err = capsys.readouterr().err
    assert "Traceback" not in err and 'File "' not in err


def test_startup_error_uses_a_message_box_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    class FakeUser32:
        @staticmethod
        def MessageBoxW(*args: Any) -> int:  # noqa: N802 - Win32 naming
            calls.append(args)
            return 1

    monkeypatch.setattr(system, "current_platform", lambda: "win32")
    monkeypatch.setitem(
        __import__("sys").modules,
        "ctypes",
        type("C", (), {"windll": type("W", (), {"user32": FakeUser32})}),
    )
    system.show_startup_error("nope", "MD-XYZ", None)
    assert calls and "MD-XYZ" in calls[0][1]


def test_startup_error_uses_osascript_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(system, "current_platform", lambda: "darwin")
    monkeypatch.setattr(
        system.subprocess, "run", lambda command, **kw: captured.append((command, kw))
    )
    system.show_startup_error("nope", "MD-XYZ", None)

    command, kwargs = captured[0]
    assert command[0] == "osascript" and isinstance(command, list)
    assert kwargs.get("shell") is not True
    assert kwargs.get("timeout")


# --- open log folder ---------------------------------------------------


def test_open_log_folder_takes_no_caller_supplied_path() -> None:
    """Same rule as Open Downloads Folder: no path may come from outside."""
    import inspect

    params = list(inspect.signature(system.open_log_folder).parameters)
    assert params == ["env"]


def test_open_log_folder_targets_the_log_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("media_downloader.paths.current_platform", lambda: "linux")
    monkeypatch.setattr(system, "open_folder", opened.append)
    system.open_log_folder({"XDG_DATA_HOME": str(tmp_path)})

    assert opened == [tmp_path / "media-downloader" / "logs"]
