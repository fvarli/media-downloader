"""Extracting exactly the members we expect, and nothing else.

Archive extraction is the most dangerous step in installing a downloaded tool:
a hostile archive can name a member ``../../.ssh/authorized_keys`` and a naive
extractor will happily write it. Rather than extract everything and hope, this
module takes the list of members the manifest says it wants and refuses
anything that is not on that list or that tries to escape the destination.

Symlinks, hard links, devices and absolute or parent-relative paths are all
rejected outright -- none of them appear in the tool archives we pin, so
tolerating them would only add risk.
"""

from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

from media_downloader.errors import MediaDownloaderError


class ArchiveError(MediaDownloaderError):
    """An archive was malformed, hostile, or missing an expected member."""


def is_safe_member_name(name: str) -> bool:
    """True when ``name`` is a plain relative path that stays inside the target.

    Both separator conventions are checked regardless of host OS, so an archive
    built to attack Windows is still rejected on Linux.
    """
    if not name or name in {".", ".."}:
        return False
    if "\x00" in name:
        return False
    # Absolute in either convention, including drive letters and UNC paths.
    if name.startswith(("/", "\\")) or PureWindowsPath(name).is_absolute():
        return False
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return not any(part == ".." for part in parts)


def _safe_destination(root: Path, name: str, destination: Path) -> Path:
    """Resolve where a member may be written, or refuse."""
    if not is_safe_member_name(name):
        raise ArchiveError(f"Refusing unsafe archive member: {name!r}")
    target = (root / destination).resolve()
    root_resolved = root.resolve()
    # Belt and braces: even a name that passed the textual check must resolve
    # inside the destination directory.
    if root_resolved != target and root_resolved not in target.parents:
        raise ArchiveError(f"Archive member would escape the target directory: {name!r}")
    return target


def extract_members(
    archive: Path,
    destination: Path,
    members: Mapping[str, str],
) -> dict[str, Path]:
    """Extract selected members of ``archive`` into ``destination``.

    ``members`` maps a logical name (``"ffmpeg"``) to the path inside the
    archive. Only those members are written; everything else is ignored.

    Returns the logical name to written path mapping.

    Raises:
        ArchiveError: if the archive is unreadable, a member is missing, or any
            member is unsafe.
    """
    suffixes = "".join(archive.suffixes[-2:]).lower()
    if archive.suffix.lower() == ".zip":
        return _extract_zip(archive, destination, members)
    if suffixes.endswith((".tar.xz", ".tar.gz", ".tar.bz2")) or archive.suffix == ".tar":
        return _extract_tar(archive, destination, members)
    raise ArchiveError(f"Unsupported archive type: {archive.name}")


def _wanted(members: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    """Match manifest member paths against the archive's actual names.

    Archives often nest everything under a version-stamped top directory whose
    exact name we would rather not pin, so a manifest entry may name a suffix
    such as ``bin/ffmpeg`` and match ``ffmpeg-n9.0.1-.../bin/ffmpeg``.
    """
    actual = list(names)
    resolved: dict[str, str] = {}
    for logical, wanted in members.items():
        wanted_posix = wanted.replace("\\", "/").lstrip("./")
        for name in actual:
            normalised = name.replace("\\", "/")
            if normalised == wanted_posix or normalised.endswith("/" + wanted_posix):
                resolved[logical] = name
                break
        else:
            raise ArchiveError(f"Archive does not contain the expected member: {wanted!r}")
    return resolved


def _extract_zip(archive: Path, destination: Path, members: Mapping[str, str]) -> dict[str, Path]:
    written: dict[str, Path] = {}
    try:
        with zipfile.ZipFile(archive) as zf:
            resolved = _wanted(members, (i.filename for i in zf.infolist()))
            for logical, name in resolved.items():
                info = zf.getinfo(name)
                if info.is_dir():
                    raise ArchiveError(f"Expected a file but found a directory: {name!r}")
                target = _safe_destination(destination, name, Path(logical))
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as out:
                    _copy(source, out)
                written[logical] = target
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"Could not read the archive: {exc}") from exc
    return written


def _extract_tar(archive: Path, destination: Path, members: Mapping[str, str]) -> dict[str, Path]:
    written: dict[str, Path] = {}
    try:
        with tarfile.open(archive) as tf:
            entries = tf.getmembers()
            resolved = _wanted(members, (m.name for m in entries))
            by_name = {m.name: m for m in entries}
            for logical, name in resolved.items():
                member = by_name[name]
                # Only ordinary files. Links and devices have no legitimate
                # place in the tool archives we pin.
                if not member.isfile():
                    raise ArchiveError(f"Refusing non-regular archive member: {name!r}")
                target = _safe_destination(destination, name, Path(logical))
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:  # pragma: no cover - isfile() already guarantees this
                    raise ArchiveError(f"Archive member could not be read: {name!r}")
                with source, target.open("wb") as out:
                    _copy(source, out)
                written[logical] = target
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"Could not read the archive: {exc}") from exc
    return written


def _copy(source: object, out: object, chunk: int = 1024 * 1024) -> None:
    """Stream a member out in chunks; tool binaries are far too big to slurp."""
    read = source.read  # type: ignore[attr-defined]
    write = out.write  # type: ignore[attr-defined]
    while True:
        block = read(chunk)
        if not block:
            return
        write(block)
