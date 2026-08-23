"""Write a machine-readable description of a freshly built artifact.

Validation metadata, not a release manifest: enough to tell two builds apart
and to know exactly what went into one when a report comes back from a user.

Contains no secrets, no environment dump and no local paths beyond the artifact
directory it was asked about.
"""

from __future__ import annotations

import hashlib
import json
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


def build_info(artifact: Path) -> dict[str, Any]:
    from media_downloader import __version__

    return {
        "name": "media-downloader",
        "version": __version__,
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
    if len(sys.argv) < 3:
        print("usage: artifact_info.py <artifact-dir> <output.json> [packed-file]")
        return 2

    artifact = Path(sys.argv[1])
    output = Path(sys.argv[2])
    info = build_info(artifact)

    if len(sys.argv) > 3:
        packed = Path(sys.argv[3])
        if packed.is_file():
            info["packed"] = {
                "name": packed.name,
                "bytes": packed.stat().st_size,
                "sha256": sha256_of(packed),
            }

    output.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
