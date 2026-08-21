"""Output directory resolution and filename-template validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader.errors import OutputError
from media_downloader.naming import (
    ensure_output_dir,
    resolve_output_dir,
    validate_filename_template,
)


def test_resolve_output_dir_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    resolved = resolve_output_dir("~/Videos")
    assert resolved.is_absolute()
    assert resolved.name == "Videos"


def test_resolve_output_dir_returns_absolute_path(tmp_path: Path) -> None:
    assert resolve_output_dir(tmp_path / "a" / "b").is_absolute()


def test_resolve_output_dir_does_not_create_anything(tmp_path: Path) -> None:
    target = tmp_path / "not-created"
    resolve_output_dir(target)
    assert not target.exists()


def test_ensure_output_dir_creates_nested_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert ensure_output_dir(target) == target
    assert target.is_dir()


def test_ensure_output_dir_is_idempotent(tmp_path: Path) -> None:
    ensure_output_dir(tmp_path / "x")
    ensure_output_dir(tmp_path / "x")
    assert (tmp_path / "x").is_dir()


def test_ensure_output_dir_rejects_a_file(tmp_path: Path) -> None:
    target = tmp_path / "afile"
    target.write_text("data")
    with pytest.raises(OutputError):
        ensure_output_dir(target)


@pytest.mark.parametrize(
    "template",
    [
        "%(title)s.%(ext)s",
        "%(title)s [%(id)s].%(ext)s",
        "video.mp4",
        "%(uploader)s - %(title)s.%(ext)s",
    ],
)
def test_validate_filename_template_accepts_plain_names(template: str) -> None:
    assert validate_filename_template(template) == template


@pytest.mark.parametrize(
    "template",
    [
        "",
        "   ",
        "..",
        ".",
        "../%(title)s.%(ext)s",
        "../../etc/passwd",
        "sub/dir/%(title)s.%(ext)s",
        "sub\\dir\\%(title)s.%(ext)s",
        "/etc/passwd",
        "/absolute.mp4",
        "C:\\Windows\\evil.mp4",
        "D:/data/x.mp4",
        "\\\\server\\share\\x.mp4",
        "//server/share/x.mp4",
        "name\x00.mp4",
        "name\n.mp4",
    ],
)
def test_validate_filename_template_blocks_directory_escape(template: str) -> None:
    """A template must never be able to write outside --output."""
    with pytest.raises(OutputError):
        validate_filename_template(template)


def test_template_rules_are_identical_on_every_platform() -> None:
    """Windows-style paths are rejected on Linux too, and vice versa."""
    for template in ("C:\\x.mp4", "a\\b.mp4", "/x.mp4", "a/b.mp4"):
        with pytest.raises(OutputError):
            validate_filename_template(template)
