"""Install a managed tool for real and prove discovery finds it.

Run on a CI runner against the pinned manifest, with any system copy hidden so
the managed one is genuinely what gets found. A target with no manifest entry
is reported as unsupported and the check passes -- that is the honest outcome
for macOS FFmpeg, which has no provider meeting the licensing and provenance
requirements, and it must not be dressed up as a failure or as a success.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from media_downloader.tools.manager import ToolManager
from media_downloader.tools.manifest import executable_name, lookup


def main() -> int:
    tool = sys.argv[1]
    spec = lookup(tool, sys.platform, __import__("platform").machine())

    if spec is None:
        print(f"  {tool}: no verified source for this platform -- reported as unsupported.")
        print("  This is a recorded limitation, not a build failure.")
        # Prove the application agrees rather than just asserting it here.
        manager = ToolManager()
        status = manager.status(tool, system_path=None)
        assert status.state.value == "unsupported", status
        assert not status.can_install, "an unsupported tool must never offer an install"
        print("  application reports: unsupported, install not offered  PASS")
        return 0

    print(f"  {tool} {spec.version} ({spec.licence})")
    print(f"    url    {spec.url}")
    print(f"    sha256 {spec.sha256}")

    with tempfile.TemporaryDirectory() as tmp:
        env = {"XDG_DATA_HOME": tmp, "LOCALAPPDATA": tmp}
        manager = ToolManager(env=env)

        assert manager.managed_dir(tool) is None, "should start uninstalled"
        installed = manager.install(tool)
        print(f"    installed -> {installed}")

        for logical in spec.executables:
            path = installed / logical
            assert path.is_file(), f"missing {logical}"
            size = path.stat().st_size
            executable = os.access(path, os.X_OK) or os.name == "nt"
            print(f"    {logical}: {size / 1e6:.1f} MB executable={executable}")
            assert size > 0 and executable

        # The binary must actually run, not merely exist.
        first = installed / next(iter(spec.executables))
        result = subprocess.run(
            [str(first), "-version" if tool == "ffmpeg" else "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[:400]
        print(f"    runs: {result.stdout.splitlines()[0][:70]}")

        # And discovery must find it with no system copy in the way.
        assert manager.managed_dir(tool) is not None
        logical = executable_name(spec, tool)
        assert manager.managed_path(tool, logical) is not None
        print("    discovery reports the managed copy  PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
