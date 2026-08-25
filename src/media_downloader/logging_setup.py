"""Logging configuration for the CLI."""

from __future__ import annotations

import logging
import logging.handlers

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME = "media_downloader"


def configure_logging(
    console: Console, *, verbose: bool = False, quiet: bool = False
) -> logging.Logger:
    """Attach a single Rich handler to the application logger.

    Only this project's logger is configured; the root logger is left alone so
    importing the package never hijacks logging in a host application.
    """
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # Replace only the console handler. The rotating file handler is attached
    # separately -- before arguments are even parsed, in a packaged build --
    # and removing it here meant everything logged afterwards never reached the
    # support report: the compatibility decisions, the job lifecycle, all of it
    # visible on screen and absent from the file somebody would send in.
    for existing in list(logger.handlers):
        if isinstance(existing, logging.handlers.RotatingFileHandler):
            continue
        logger.removeHandler(existing)

    handler = RichHandler(
        console=console,
        show_time=False,
        show_path=verbose,
        rich_tracebacks=verbose,
        markup=False,
    )
    handler.setLevel(level)
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the application logger, or a child of it."""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")
