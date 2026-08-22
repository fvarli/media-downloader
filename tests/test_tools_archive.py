"""Archive extraction, including deliberately hostile archives.

This is the most dangerous step in installing a downloaded tool, so the
malicious cases get more attention than the happy path.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from media_downloader.tools.archive import ArchiveError, extract_members, is_safe_member_name


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def make_tar(path: Path, entries: dict[str, bytes], mode: str = "w:xz") -> Path:
    with tarfile.open(path, mode) as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


# -- name validation ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["ffmpeg", "bin/ffmpeg", "ffmpeg-9.0/bin/ffprobe", "a/b/c/deno"],
)
def test_plain_relative_names_are_accepted(name: str) -> None:
    assert is_safe_member_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../ffmpeg",
        "bin/../../ffmpeg",
        "a/../../b",
        "/etc/passwd",
        "/usr/bin/ffmpeg",
        "\\windows\\system32\\evil.exe",
        "C:/Windows/evil.exe",
        "C:\\Windows\\evil.exe",
        "//server/share/evil",
        "bin\\..\\..\\evil",
        "name\x00.exe",
    ],
)
def test_escaping_names_are_rejected(name: str) -> None:
    """Both separator conventions are checked regardless of host OS."""
    assert not is_safe_member_name(name)


# -- extraction ---------------------------------------------------------


def test_extracts_only_the_requested_members(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "a.zip",
        {
            "bin/ffmpeg": b"FFMPEG",
            "bin/ffprobe": b"PROBE",
            "bin/ffplay": b"PLAY",
            "doc/readme.txt": b"docs",
        },
    )
    out = tmp_path / "out"
    written = extract_members(archive, out, {"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"})

    assert set(written) == {"ffmpeg", "ffprobe"}
    assert written["ffmpeg"].read_bytes() == b"FFMPEG"
    # ffplay and the docs were in the archive but are not on our list.
    assert not (out / "ffplay").exists()
    assert not (out / "doc").exists()


def test_matches_a_versioned_top_level_directory(tmp_path: Path) -> None:
    """The real FFmpeg archive nests everything under a version-stamped dir."""
    archive = make_tar(
        tmp_path / "a.tar.xz",
        {
            "ffmpeg-n9.0.1-6-g9d4ca21220-linux64-lgpl-9.0/bin/ffmpeg": b"X",
            "ffmpeg-n9.0.1-6-g9d4ca21220-linux64-lgpl-9.0/bin/ffprobe": b"Y",
        },
    )
    written = extract_members(
        archive, tmp_path / "out", {"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"}
    )
    assert written["ffmpeg"].read_bytes() == b"X"
    assert written["ffprobe"].read_bytes() == b"Y"


def test_flat_archive_member_matches(tmp_path: Path) -> None:
    """The Deno zip has the binary at the top level."""
    archive = make_zip(tmp_path / "d.zip", {"deno": b"DENO"})
    written = extract_members(archive, tmp_path / "out", {"deno": "deno"})
    assert written["deno"].read_bytes() == b"DENO"


def test_extracted_files_land_under_the_destination(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {"bin/ffmpeg": b"X"})
    out = tmp_path / "out"
    written = extract_members(archive, out, {"ffmpeg": "bin/ffmpeg"})
    assert out.resolve() in written["ffmpeg"].resolve().parents


# -- hostile archives ---------------------------------------------------


@pytest.mark.parametrize("member", ["../escaped", "../../escaped", "/abs/escaped"])
def test_zip_slip_is_refused(tmp_path: Path, member: str) -> None:
    archive = make_zip(tmp_path / "evil.zip", {member: b"PWNED"})
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"tool": member})
    assert not (tmp_path / "escaped").exists()
    assert not Path("/abs/escaped").exists()


@pytest.mark.parametrize("member", ["../escaped", "../../../escaped"])
def test_tar_slip_is_refused(tmp_path: Path, member: str) -> None:
    archive = make_tar(tmp_path / "evil.tar.xz", {member: b"PWNED"})
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"tool": member})
    assert not (tmp_path / "escaped").exists()


def test_a_symlink_member_is_refused(tmp_path: Path) -> None:
    """A symlink could redirect a later write outside the destination."""
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo("ffmpeg")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"ffmpeg": "ffmpeg"})


def test_a_device_member_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo("ffmpeg")
        info.type = tarfile.CHRTYPE
        tf.addfile(info)
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"ffmpeg": "ffmpeg"})


def test_a_directory_where_a_file_was_expected_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/ffmpeg/", b"")
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"ffmpeg": "bin/ffmpeg"})


def test_a_missing_expected_member_is_an_error(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {"bin/ffmpeg": b"X"})
    with pytest.raises(ArchiveError, match="ffprobe"):
        extract_members(archive, tmp_path / "out", {"ffprobe": "bin/ffprobe"})


def test_a_corrupt_archive_is_an_error(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip file at all")
    with pytest.raises(ArchiveError):
        extract_members(archive, tmp_path / "out", {"x": "x"})


def test_an_unknown_archive_type_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "thing.rar"
    archive.write_bytes(b"x")
    with pytest.raises(ArchiveError, match="Unsupported"):
        extract_members(archive, tmp_path / "out", {"x": "x"})
