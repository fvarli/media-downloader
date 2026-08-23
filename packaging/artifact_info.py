"""Write a machine-readable description of a freshly built artifact.

Validation metadata, not a release manifest: enough to tell two builds apart
and to know exactly what went into one when a report comes back from a user.

Contains no secrets, no environment dump and no local paths beyond the artifact
directory it was asked about.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def _version(module: str, attribute: str = "__version__") -> str:
    try:
        mod = __import__(module, fromlist=[attribute])
        return str(getattr(mod, attribute))
    except Exception:
        return "unknown"


def _managed_tool(tool: str) -> dict[str, Any] | None:
    """What the manifest pins for this tool on this platform, if anything."""
    from media_downloader.tools.manifest import lookup

    spec = lookup(tool, sys.platform, platform.machine())
    if spec is None:
        return None
    return {
        "version": spec.version,
        "licence": spec.licence,
        "source": spec.source,
        "sha256": spec.sha256,
    }


def build_info(artifact: Path, *, commit: str, mode: str) -> dict[str, Any]:
    from media_downloader import __version__

    return {
        "name": "media-downloader",
        "version": __version__,
        # Which source this artifact was actually built from, and whether it
        # has a console attached. Both matter to somebody holding a downloaded
        # archive and asking "is this the build we discussed?" -- and neither
        # can be recovered by looking at the files.
        "commit": commit,
        "mode": mode,
        "built_on": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "pyinstaller": _version("PyInstaller"),
        "yt_dlp": _version("yt_dlp.version"),
        "artifact": {
            "path": artifact.name,
            "bytes": sum(f.stat().st_size for f in artifact.rglob("*") if f.is_file()),
            "files": sum(1 for f in artifact.rglob("*") if f.is_file()),
        },
        # null means "no verified source for this platform yet" -- notably
        # macOS FFmpeg, which has no provider meeting our requirements.
        "managed_tools": {
            "ffmpeg": _managed_tool("ffmpeg"),
            "deno": _managed_tool("deno"),
        },
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="the built application directory or bundle")
    parser.add_argument("output", type=Path, help="where to write the JSON")
    parser.add_argument(
        "packed",
        type=Path,
        nargs="?",
        help="the distributable archive, if one has been built; its SHA-256 is recorded",
    )
    parser.add_argument(
        "--commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
        help="source commit; defaults to GITHUB_SHA",
    )
    parser.add_argument("--mode", choices=("console", "windowed"), default="console")
    args = parser.parse_args()

    info = build_info(args.artifact, commit=args.commit, mode=args.mode)

    if args.packed is not None and args.packed.is_file():
        info["packed"] = {
            "name": args.packed.name,
            "bytes": args.packed.stat().st_size,
            "sha256": sha256_of(args.packed),
        }

    args.output.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
