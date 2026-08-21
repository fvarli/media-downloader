"""Command-line interface: argument parsing, orchestration and exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from rich.console import Console

from media_downloader import __version__
from media_downloader.config import (
    AUDIO_FORMAT_CHOICES,
    ENV_FFMPEG_LOCATION,
    ENV_OUTPUT_DIR,
    LOSSLESS_AUDIO_FORMAT,
    QUALITY_CHOICES,
    DownloadRequest,
    build_request,
    default_output_dir,
)
from media_downloader.downloader import Downloader, MediaInfo
from media_downloader.errors import ExitCode, MediaDownloaderError
from media_downloader.ffmpeg import FFMPEG_GUIDANCE, FFmpegStatus, detect_ffmpeg
from media_downloader.jsruntime import (
    JS_RUNTIME_GUIDANCE,
    JSRuntimeStatus,
    detect_js_runtime,
)
from media_downloader.logging_setup import configure_logging
from media_downloader.naming import DEFAULT_OUTPUT_TEMPLATE
from media_downloader.progress import ProgressReporter
from media_downloader.urls import SUPPORTED_SERVICE_NAMES, detect_service, validate_url

PROGRAM_NAME = "media-downloader"

EPILOG = f"""\
examples:
  {PROGRAM_NAME} "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  {PROGRAM_NAME} "URL" --audio
  {PROGRAM_NAME} "URL" --quality 1080
  {PROGRAM_NAME} "URL" --output ~/Downloads
  {PROGRAM_NAME} "URL" --info

environment variables:
  {ENV_OUTPUT_DIR}    default download directory
  {ENV_FFMPEG_LOCATION}    directory containing ffmpeg and ffprobe

Explicitly supported services: {", ".join(SUPPORTED_SERVICE_NAMES)}.
Other public URLs are attempted through yt-dlp on a best-effort basis.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="Download publicly accessible media from a URL using yt-dlp and FFmpeg.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Public http(s) URL of the media to download.")
    parser.add_argument(
        "-o",
        "--output",
        metavar="DIR",
        default=None,
        help=f"Directory to save into (default: ./{default_output_dir().name}).",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=QUALITY_CHOICES,
        default="best",
        help="Maximum video height, or best/worst (default: best).",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Download audio only.",
    )
    parser.add_argument(
        "--audio-format",
        choices=AUDIO_FORMAT_CHOICES,
        default=LOSSLESS_AUDIO_FORMAT,
        help=(
            "Audio codec for --audio (default: best, which keeps the original "
            "stream without re-encoding). Any other value requires FFmpeg."
        ),
    )
    parser.add_argument(
        "--filename",
        metavar="TEMPLATE",
        default=None,
        # argparse applies %-formatting to help strings, so % must be doubled.
        help=(
            "yt-dlp output template for the file name "
            f"(default: {DEFAULT_OUTPUT_TEMPLATE.replace('%', '%%')})."
        ),
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show media metadata and exit without downloading.",
    )
    parser.add_argument(
        "--ffmpeg-location",
        metavar="PATH",
        default=None,
        help="Directory containing ffmpeg and ffprobe, if not on PATH.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing file instead of keeping it.",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug output, including yt-dlp's."
    )
    verbosity.add_argument(
        "--quiet", action="store_true", help="Only print the final path and errors."
    )

    parser.add_argument("--version", action="version", version=f"{PROGRAM_NAME} {__version__}")
    return parser


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "unknown"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_size(size: int | None) -> str:
    if not size:
        return "unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - unreachable, loop always returns


def _print_info(console: Console, info: MediaInfo) -> None:
    """Print the ``--info`` report."""
    from rich.table import Table

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")

    table.add_row("Title", info.title)
    table.add_row("Uploader", info.uploader or "unknown")
    table.add_row("Duration", _format_duration(info.duration_seconds))
    table.add_row("Extractor", info.extractor)
    if info.width and info.height:
        table.add_row("Resolution", f"{info.width}x{info.height}")
    if info.ext:
        table.add_row("Container", info.ext)
    table.add_row("Approx. size", _format_size(info.filesize_bytes))
    if info.webpage_url:
        table.add_row("URL", info.webpage_url)

    console.print(table)


def _announce_service(console: Console, url: str, *, quiet: bool) -> None:
    """Report which service was detected, or note that it is unrecognised."""
    if quiet:
        return
    service = detect_service(url)
    if service is not None:
        console.print(f"[dim]Detected service:[/dim] {service.name}")
    else:
        console.print(
            "[yellow]Note:[/yellow] this URL is not one of the explicitly supported "
            "services; attempting it through yt-dlp anyway."
        )


def _warn_about_missing_tools(
    console: Console,
    request: DownloadRequest,
    ffmpeg: FFmpegStatus,
    js_runtime: JSRuntimeStatus,
    *,
    quiet: bool,
) -> None:
    """Explain, once and in plain language, what missing tools will cost."""
    if quiet:
        return

    if not ffmpeg.available:
        console.print(f"[yellow]Warning:[/yellow] {FFMPEG_GUIDANCE}")
        # An explicit conversion request is about to be refused outright, so
        # promising to continue would contradict the error that follows.
        if not request.needs_audio_conversion:
            fallback = (
                "the original audio stream will be saved as-is, without conversion."
                if request.audio_only
                else "only pre-merged formats will be used, so the available "
                "quality may be lower than usual."
            )
            console.print(f"[yellow]Continuing:[/yellow] {fallback}")

    service = detect_service(request.url)
    if service is not None and service.key == "youtube" and not js_runtime.available:
        console.print(f"[yellow]Warning:[/yellow] {JS_RUNTIME_GUIDANCE}")


def run(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    console = Console(stderr=True)
    out_console = Console()
    configure_logging(console, verbose=args.verbose, quiet=args.quiet)

    try:
        url = validate_url(args.url)
        request = build_request(
            url=url,
            output=args.output,
            quality=args.quality,
            audio_only=args.audio,
            audio_format=args.audio_format,
            filename=args.filename,
            ffmpeg_location=args.ffmpeg_location,
            info_only=args.info,
            overwrite=args.overwrite,
        )

        _announce_service(console, url, quiet=args.quiet)
        ffmpeg = detect_ffmpeg(request.ffmpeg_location)
        js_runtime = detect_js_runtime()

        if request.info_only:
            downloader = Downloader(ffmpeg, js_runtime=js_runtime, verbose=args.verbose)
            _print_info(out_console, downloader.fetch_info(request))
            return int(ExitCode.SUCCESS)

        _warn_about_missing_tools(console, request, ffmpeg, js_runtime, quiet=args.quiet)

        with ProgressReporter(console, enabled=not args.quiet) as reporter:
            downloader = Downloader(
                ffmpeg,
                js_runtime=js_runtime,
                progress_hook=reporter.hook,
                verbose=args.verbose,
            )
            result = downloader.download(request)

        if not args.quiet:
            console.print("[green]Download complete.[/green]")
        # The final path goes to stdout on its own so it can be piped.
        # markup=False: a filename may legitimately contain square brackets,
        # which Rich would otherwise parse as a style tag and strip.
        out_console.print(str(result.path), highlight=False, soft_wrap=True, markup=False)
        return int(ExitCode.SUCCESS)

    except MediaDownloaderError as exc:
        console.print(f"[red]Error:[/red] {exc.message}")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        return int(exc.exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return int(ExitCode.INTERRUPTED)
    except Exception as exc:
        if args.verbose:
            console.print_exception()
        else:
            console.print(f"[red]Unexpected error:[/red] {exc}")
            console.print("[dim]Run again with --verbose for a full traceback.[/dim]")
        return int(ExitCode.UNEXPECTED_ERROR)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    return run(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
