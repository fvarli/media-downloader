"""Starting and stopping the web UI from the CLI. No browser is ever opened."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from rich.console import Console

from media_downloader.web import launcher


@pytest.fixture
def console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=120), buffer


class RecordingServer:
    """Stands in for WebServer; records the lifecycle without a socket."""

    instances: ClassVar[list[RecordingServer]] = []

    def __init__(self, config: Any, *, interrupt: bool = False) -> None:
        self.config = config
        self.url = "http://127.0.0.1:9999"
        self.served = False
        self.shut_down = False
        self._interrupt = interrupt
        RecordingServer.instances.append(self)

    def serve_forever(self) -> None:
        self.served = True
        if self._interrupt:
            raise KeyboardInterrupt

    def shutdown(self) -> None:
        self.shut_down = True


@pytest.fixture(autouse=True)
def _reset() -> None:
    RecordingServer.instances.clear()


def install(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> None:
    monkeypatch.setattr(launcher, "WebServer", lambda config: RecordingServer(config, **kwargs))
    monkeypatch.setattr(launcher, "open_browser", lambda url: True)


def test_the_url_is_always_printed(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    """Printing the URL is what makes a headless machine merely degraded."""
    install(monkeypatch, interrupt=True)
    out, buffer = console
    launcher.serve(out, download_dir=tmp_path, open_browser_on_start=False)
    text = buffer.getvalue()
    assert "http://127.0.0.1:9999" in text
    assert str(tmp_path) in text
    assert "Ctrl+C" in text


def test_ctrl_c_shuts_down_cleanly_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    install(monkeypatch, interrupt=True)
    out, _ = console
    assert launcher.serve(out, download_dir=tmp_path, open_browser_on_start=False) == 0
    assert RecordingServer.instances[0].shut_down is True


def test_the_server_is_shut_down_even_if_serving_raises(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    class Exploding(RecordingServer):
        def serve_forever(self) -> None:
            raise RuntimeError("socket exploded")

    monkeypatch.setattr(launcher, "WebServer", lambda config: Exploding(config))
    monkeypatch.setattr(launcher, "open_browser", lambda url: True)
    out, _ = console
    with pytest.raises(RuntimeError):
        launcher.serve(out, download_dir=tmp_path, open_browser_on_start=False)
    assert RecordingServer.instances[0].shut_down is True


def test_the_browser_is_opened_with_our_url(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        launcher, "WebServer", lambda config: RecordingServer(config, interrupt=True)
    )
    monkeypatch.setattr(launcher, "open_browser", lambda url: opened.append(url) or True)
    monkeypatch.setattr(launcher, "BROWSER_DELAY_SECONDS", 0.0)
    out, _ = console
    launcher.serve(out, download_dir=tmp_path, open_browser_on_start=True)
    # The timer thread is given a moment to fire.
    import time

    for _ in range(100):
        if opened:
            break
        time.sleep(0.01)
    assert opened == ["http://127.0.0.1:9999"]


def test_a_machine_without_a_browser_still_serves(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        launcher, "WebServer", lambda config: RecordingServer(config, interrupt=True)
    )
    monkeypatch.setattr(launcher, "open_browser", lambda url: False)
    monkeypatch.setattr(launcher, "BROWSER_DELAY_SECONDS", 0.0)
    # Without this the fallback runs for real, and on macOS and Windows that is
    # a modal dialog that blocks until somebody clicks it -- which nobody will
    # on a CI runner.
    shown: list[str] = []
    monkeypatch.setattr(launcher, "report_startup_url", shown.append)

    out, _ = console
    assert launcher.serve(out, download_dir=tmp_path, open_browser_on_start=True) == 0

    for _ in range(100):
        if shown:
            break
        time.sleep(0.01)
    assert shown == ["http://127.0.0.1:9999"]


def test_the_default_download_directory_is_used_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    install(monkeypatch, interrupt=True)
    monkeypatch.setattr(launcher, "default_download_dir", lambda: tmp_path / "Chosen")
    out, _ = console
    launcher.serve(out, open_browser_on_start=False)
    assert RecordingServer.instances[0].config.download_dir == tmp_path / "Chosen"


# -- internal no-browser switch ------------------------------------------


def _serve_and_capture_browser(
    monkeypatch: pytest.MonkeyPatch,
    console: tuple[Console, io.StringIO],
    tmp_path: Path,
    env: dict[str, str],
) -> int:
    opened: list[str] = []
    monkeypatch.setattr(
        launcher, "WebServer", lambda config: RecordingServer(config, interrupt=True)
    )
    monkeypatch.setattr(launcher, "open_browser", lambda url: opened.append(url) or True)
    monkeypatch.setattr(launcher, "BROWSER_DELAY_SECONDS", 0.0)
    # The fallback is a modal dialog on macOS and Windows; never let it run.
    monkeypatch.setattr(launcher, "report_startup_url", lambda url: None)

    out, _ = console
    launcher.serve(out, download_dir=tmp_path, open_browser_on_start=True, env=env)
    for _ in range(100):
        if opened:
            break
        time.sleep(0.01)
    return len(opened)


def test_the_browser_opens_by_default(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    assert _serve_and_capture_browser(monkeypatch, console, tmp_path, env={}) == 1


def test_the_internal_switch_suppresses_the_browser(
    monkeypatch: pytest.MonkeyPatch, console: tuple[Console, io.StringIO], tmp_path: Path
) -> None:
    """Automated checks must never open a browser or a modal dialog.

    A windowed build that cannot launch a browser falls back to a dialog, and
    an unattended runner has nobody to dismiss it.
    """
    env = {"MD_NO_BROWSER": "1"}
    assert _serve_and_capture_browser(monkeypatch, console, tmp_path, env=env) == 0


@pytest.mark.parametrize("env", [{"MD_NO_BROWSER": "0"}, {"MD_NO_BROWSER": "true"}, {"OTHER": "1"}])
def test_only_the_exact_value_suppresses_the_browser(
    monkeypatch: pytest.MonkeyPatch,
    console: tuple[Console, io.StringIO],
    tmp_path: Path,
    env: dict[str, str],
) -> None:
    assert _serve_and_capture_browser(monkeypatch, console, tmp_path, env=env) == 1
