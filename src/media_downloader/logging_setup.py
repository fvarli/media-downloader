"""Logging configuration for the CLI."""

from __future__ import annotations

import logging

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

    for existing in list(logger.handlers):
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
