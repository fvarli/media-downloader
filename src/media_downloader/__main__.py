"""Entry point for ``python -m media_downloader``."""

from __future__ import annotations

import sys

from media_downloader.cli import main

if __name__ == "__main__":
    sys.exit(main())
