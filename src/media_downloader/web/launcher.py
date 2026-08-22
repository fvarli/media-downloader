"""Starting the web UI from the command line.

Binds loopback, opens a browser, and serves until interrupted. The URL is
always printed, so a machine with no browser -- headless, SSH, WSL -- is a
degraded experience rather than a failure.
"""

from __future__ import annotations

import threading
from pathlib import Path

from rich.console import Console

from media_downloader.service import detect_environment
from media_downloader.web.server import PREFERRED_PORT, WebAppConfig, WebServer
from media_downloader.web.system import (
    default_download_dir,
    open_browser,
    report_startup_url,
)

#: Give the server a moment to accept connections before the browser asks.
BROWSER_DELAY_SECONDS = 0.4


def _launch_browser(url: str) -> None:
    """Open the browser, or make sure the user sees the address regardless."""
    if not open_browser(url):
        report_startup_url(url)


def serve(
    console: Console,
    *,
    download_dir: Path | None = None,
    port: int = PREFERRED_PORT,
    open_browser_on_start: bool = True,
    verbose: bool = False,
) -> int:
    """Run the local web UI until interrupted. Returns a process exit code."""
    target_dir = download_dir or default_download_dir()
    config = WebAppConfig(
        download_dir=target_dir,
        environment=detect_environment(),
        port=port,
        verbose=verbose,
    )
    server = WebServer(config)

    console.print(f"[bold]Media Downloader[/bold] is running at [cyan]{server.url}[/cyan]")
    console.print(f"[dim]Saving downloads to {target_dir}[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    if open_browser_on_start:
        # Deferred so the socket is definitely accepting before the browser
        # asks. If no browser can be opened -- headless, SSH, or a packaged app
        # with no console -- the address is surfaced some other way instead.
        threading.Timer(BROWSER_DELAY_SECONDS, _launch_browser, args=(server.url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping...[/yellow]")
    finally:
        server.shutdown()

    return 0
