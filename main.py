"""Run the CLI straight from a source checkout: ``python main.py "URL"``.

Adds ``src/`` to the import path only when the package is not already
installed, so the same file works before and after ``pip install -e .``.
"""

from __future__ import annotations

import sys
from importlib.util import find_spec
from pathlib import Path

if find_spec("media_downloader") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from media_downloader.cli import main

if __name__ == "__main__":
    sys.exit(main())
