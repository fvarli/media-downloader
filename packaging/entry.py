"""Entry point for the frozen application.

PyInstaller needs a module to start from, and it must not be the development
shim in ``main.py``: that one manipulates ``sys.path`` relative to ``__file__``,
which has no meaning inside a bundle.
"""

from __future__ import annotations

import multiprocessing
import sys

from media_downloader.cli import main

if __name__ == "__main__":
    # Required before anything else on frozen builds: without it, any child
    # process re-runs the whole application instead of the worker function.
    multiprocessing.freeze_support()
    sys.exit(main())
