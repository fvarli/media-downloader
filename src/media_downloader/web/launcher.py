"""Starting the web UI from the command line.

Binds loopback, opens a browser, and serves until interrupted. The URL is
always printed, so a machine with no browser -- headless, SSH, WSL -- is a
degraded experience rather than a failure.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path

from rich.console import Console

from media_downloader.diagnostics import configure_file_logging, log_startup, record_error
from media_downloader.logging_setup import get_logger
from media_downloader.service import detect_environment
from media_downloader.web.server import PREFERRED_PORT, WebAppConfig, WebServer
from media_downloader.web.system import (
    default_download_dir,
    open_browser,
    report_startup_url,
    show_startup_error,
)

logger = get_logger("launcher")

#: Give the server a moment to accept connections before the browser asks.
BROWSER_DELAY_SECONDS = 0.4


def _launch_browser(url: str) -> None:
    """Open the browser, or make sure the user sees the address regardless."""
    if open_browser(url):
        logger.info("browser launched")
        return
    logger.warning("no browser could be launched; showing the address instead")
    report_startup_url(url)


def serve(
    console: Console,
    *,
    download_dir: Path | None = None,
    port: int = PREFERRED_PORT,
    open_browser_on_start: bool = True,
    verbose: bool = False,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run the local web UI until interrupted. Returns a process exit code.

    ``env`` overrides where per-user data -- logs above all -- is resolved. It
    defaults to the real environment; tests pass an isolated one so running the
    server can never append to somebody's actual diagnostics log.
    """
    # Diagnostics first: if anything below fails, the log is the only record a
    # packaged application leaves behind.
    log_path = configure_file_logging(dict(env) if env is not None else None)
    log_startup(logger)

    try:
        target_dir = download_dir or default_download_dir()
        environment = detect_environment()
        logger.info(
            "environment ffmpeg=%s js_runtime=%s downloads=%s",
            "available" if environment.ffmpeg.available else "unavailable",
            environment.js_runtime.name or "unavailable",
            target_dir,
        )
        config = WebAppConfig(
            download_dir=target_dir,
            environment=environment,
            port=port,
            verbose=verbose,
        )
        server = WebServer(config)
    except Exception as exc:
        # Before the interface exists there is nothing to show an error in, so
        # this is the last chance to tell the user anything at all.
        error_id = record_error(logger, exc, context="startup failed")
        console.print(f"[red]Media Downloader could not start:[/red] {exc}")
        console.print(f"[dim]Error ID: {error_id}[/dim]")
        show_startup_error(str(exc), error_id, log_path.parent if log_path else None)
        raise

    logger.info("server listening on %s", server.url)

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
        logger.info("shutdown complete")

    return 0
