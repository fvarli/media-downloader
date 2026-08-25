"""Verify the archive a human will actually download.

The frozen smoke test proves the *build* works. This proves the **archive**
works, which is a different claim: it is extracted with the same tool the
tester's operating system will use, and then checked for the things an archive
round trip is known to destroy.

Why it exists at all. GitHub's own artifact upload documents that it does not
preserve file permissions -- everything arrives as 644 -- so a macOS .app
downloaded that way cannot launch, before Gatekeeper is even reached. We
therefore build the distributable archive ourselves on the native runner, and
this script is the proof that doing so actually worked.

One trap worth naming: extraction here must use the platform's own tool.
Python's zipfile silently drops permission bits, so extracting with it and then
checking for the executable bit would either fail against a perfectly good
archive or, worse, be quietly meaningless. macOS uses ditto (what Archive
Utility is built on) and Linux uses tar. Windows needs no such care, because a
.exe does not depend on a permission bit.
"""

from __future__ import annotations

import argparse
import subprocess
import zipfile
from pathlib import Path

#: Paths that must exist inside the extracted payload, relative to its root.
REQUIRED_PATHS = {
    "macos": ("Contents/MacOS/media-downloader", "Contents/Info.plist"),
    "windows": ("media-downloader.exe", "_internal"),
    "linux": ("media-downloader", "_internal"),
}

#: The file that has to come back executable. Windows is None on purpose:
#: executability there is not carried by a permission bit.
MUST_BE_EXECUTABLE = {
    "macos": "Contents/MacOS/media-downloader",
    "windows": None,
    "linux": "media-downloader",
}

#: Windows loads these before Python exists, so a missing one fails in the
#: bootloader with "Failed to load Python DLL" and nothing we write in Python
#: can report it. They are present today; this keeps them present.
WINDOWS_RUNTIME_DLLS = (
    "_internal/python312.dll",
    "_internal/VCRUNTIME140.dll",
    "_internal/VCRUNTIME140_1.dll",
    "_internal/ucrtbase.dll",
)

#: What the payload's root directory should be called.
EXPECTED_ROOT = {
    "macos": "Media Downloader.app",
    "windows": "media-downloader",
    "linux": "media-downloader",
}

#: Build leftovers and scratch that must never reach a tester. The duplicate
#: PyInstaller COLLECT directory beside the .app is the specific one that got
#: shipped before: 429 files uploaded where the bundle itself is 129.
FORBIDDEN_ROOT_ENTRIES = (
    "build",
    "artifact-info.json",
    "__pycache__",
    ".pytest_cache",
    "media-downloader",  # only alongside the .app; see layout_problems
)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}")
    if not condition:
        failures.append(label)


# -- pure judgements (unit-tested offline) -------------------------------


def layout_problems(platform: str, root_entries: list[str]) -> list[str]:
    """Judge the top level of an extracted archive.

    Separate from the filesystem so it can be tested without building anything.
    """
    expected = EXPECTED_ROOT[platform]
    problems = []
    if root_entries != [expected]:
        problems.append(f"expected exactly [{expected!r}], found {sorted(root_entries)!r}")
    for entry in root_entries:
        if entry != expected and entry in FORBIDDEN_ROOT_ENTRIES:
            problems.append(f"build leftover shipped to the tester: {entry!r}")
    return problems


def is_executable(mode: int) -> bool:
    """True when any execute bit survived the round trip."""
    return bool(mode & 0o111)


# -- extraction ----------------------------------------------------------


def extract(archive: Path, destination: Path, platform: str) -> None:
    """Unpack with the tool the tester's own system would use."""
    destination.mkdir(parents=True, exist_ok=True)
    if platform == "macos":
        # ditto is what Finder's Archive Utility is built on, and the only
        # extractor that restores what ditto -c -k stored.
        subprocess.run(
            ["ditto", "-x", "-k", str(archive), str(destination)], check=True, timeout=600
        )
    elif platform == "linux":
        subprocess.run(
            ["tar", "-xzf", str(archive), "-C", str(destination)], check=True, timeout=600
        )
    else:
        # Windows: permissions are not carried by the archive anyway, so
        # zipfile is honest here rather than merely convenient.
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)


# -- checks --------------------------------------------------------------


def verify(archive: Path, destination: Path, platform: str) -> Path:
    print(f"Archive:  {archive.name} ({archive.stat().st_size / 1e6:.1f} MB)")
    print(f"Platform: {platform}\n")

    print("Extraction")
    extract(archive, destination, platform)
    root_entries = [p.name for p in destination.iterdir()]
    problems = layout_problems(platform, root_entries)
    check(
        "the archive holds exactly the payload and nothing else", not problems, "; ".join(problems)
    )

    payload = destination / EXPECTED_ROOT[platform]
    if not payload.exists():
        check("payload present", False, f"{payload} is missing")
        return payload

    print("\nStructure")
    for relative in REQUIRED_PATHS[platform]:
        check(f"{relative} present", (payload / relative).exists())

    if platform == "macos":
        # A bundle is a contract with the operating system, not just a folder.
        plist = payload / "Contents" / "Info.plist"
        text = plist.read_text(errors="replace") if plist.is_file() else ""
        check("Info.plist names the bundle", "Media Downloader" in text)
        check("Contents/MacOS is a directory", (payload / "Contents" / "MacOS").is_dir())
        support = [
            name for name in ("Frameworks", "Resources") if (payload / "Contents" / name).is_dir()
        ]
        check("Contents carries the collected payload", bool(support), str(support))

    if platform == "windows":
        # Checked because a user reported "Failed to load Python DLL" -- which
        # turned out to be an extraction that separated the .exe from
        # _internal, not a missing file. The bundle was complete, and these
        # assertions are what keep that true.
        print("\nWindows runtime")
        for relative in WINDOWS_RUNTIME_DLLS:
            target = payload / relative
            size = target.stat().st_size if target.is_file() else 0
            check(f"{Path(relative).name} bundled", size > 0, f"{size} bytes")

    print("\nPermissions after the round trip")
    relative = MUST_BE_EXECUTABLE[platform]
    if relative is None:
        print("  SKIP  Windows does not carry executability in a permission bit")
        exe = payload / "media-downloader.exe"
        check("the .exe is the entry point a double-click resolves to", exe.is_file(), str(exe))
    else:
        target = payload / relative
        mode = target.stat().st_mode if target.exists() else 0
        check(
            f"{relative} is executable",
            target.exists() and is_executable(mode),
            f"mode {mode & 0o777:o} -- GitHub's own artifact upload would have made this 644",
        )

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path, help="a fresh directory to extract into")
    parser.add_argument("--platform", required=True, choices=("macos", "windows", "linux"))
    args = parser.parse_args()

    payload = verify(args.archive.resolve(), args.destination.resolve(), args.platform)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print(f"Archive verified. Payload: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
