"""Output directory resolution and filename-template validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader.errors import OutputError
from media_downloader.naming import (
    AUTO_NAME_FIELD,
    AUTO_OUTPUT_TEMPLATE,
    build_auto_filename_stem,
    clean_auto_title,
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


# --- automatic filename cleaning ---------------------------------------
#
# Regression cases for the ugly names social platforms produce. yt-dlp's own
# sanitiser transliterates forbidden characters instead of removing them, so a
# URL in the title survives as lookalike glyphs; these tests pin the behaviour
# that removes it from the title text first.


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # The real X/Twitter case that prompted this: a bare t.co link.
        ("Trend - https://t.co/YF86pOpbhn", "Trend"),
        # A URL embedded in otherwise meaningful text.
        ("Amazing goal https://example.com/watch", "Amazing goal"),
        ("Check www.example.com/x out", "Check out"),
        # Bare host/path with no scheme, as X shortens links.
        ("t.co/abc only", "only"),
        ("http://example.com/a b", "b"),
        # A dot without a path is a sentence, not a URL.
        ("video.mp4 review", "video.mp4 review"),
        ("Dr. Who returns", "Dr. Who returns"),
    ],
)
def test_clean_auto_title_removes_urls(title: str, expected: str) -> None:
    assert clean_auto_title(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("\U0001f525 Amazing Goal \U0001f525", "Amazing Goal"),
        ("⚽ Goal ❤️", "Goal"),
        # Zero-width joiner sequences must not leave debris behind.
        ("A \U0001f468‍\U0001f4bb B", "A B"),
        ("© 2026 Studio", "2026 Studio"),
    ],
)
def test_clean_auto_title_removes_emoji_and_decorative_symbols(title: str, expected: str) -> None:
    assert clean_auto_title(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ('a:b/c\\d*e?f"g<h>i|j', "a b c d e f g h i j"),
        ("Trend: the video", "Trend the video"),
        # Splitting on a forbidden character must not fuse the words together.
        ("Trend:video", "Trend video"),
    ],
)
def test_clean_auto_title_removes_filesystem_hostile_characters(title: str, expected: str) -> None:
    assert clean_auto_title(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Trend --- video", "Trend - video"),
        ("Trend - - video", "Trend - video"),
        ("Trend -", "Trend"),
        ("- Trend", "Trend"),
        ("Trend     video", "Trend video"),
        ("  Trend  ", "Trend"),
        ("Trend —— video", "Trend - video"),
        # A single hyphen inside a word is meaningful and must survive.
        ("well-known thing", "well-known thing"),
    ],
)
def test_clean_auto_title_normalises_separators_and_whitespace(title: str, expected: str) -> None:
    assert clean_auto_title(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "İstanbul'da güzel bir gün",
        "Şu çok güzel bir ğün",
        "Москва видео",
        "東京の動画",
    ],
)
def test_clean_auto_title_never_transliterates_non_ascii_text(title: str) -> None:
    """Readable Unicode must survive; this is not an ASCII-only cleaner."""
    assert clean_auto_title(title) == title


def test_clean_auto_title_keeps_harmless_symbols() -> None:
    """Sm characters like + and = are not decorative and must stay."""
    assert clean_auto_title("1+1=2 math") == "1+1=2 math"
    assert clean_auto_title("50% off deal") == "50% off deal"
    assert clean_auto_title("Version (2) [HD]") == "Version (2) [HD]"


@pytest.mark.parametrize("title", ["", "   ", None, "https://t.co/foo", "\U0001f525"])
def test_clean_auto_title_may_legitimately_empty(title: str | None) -> None:
    assert clean_auto_title(title) == ""


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        # The reported case, end to end.
        (
            {"title": "Trend - https://t.co/YF86pOpbhn", "id": "2090546322570924033"},
            "Trend - 2090546322570924033",
        ),
        (
            {"title": "Amazing goal https://example.com/watch", "id": "12345"},
            "Amazing goal - 12345",
        ),
        ({"title": "Trend --- https://t.co/foo", "id": "123"}, "Trend - 123"),
        ({"title": "\U0001f525 Amazing Goal \U0001f525", "id": "987"}, "Amazing Goal - 987"),
        (
            {"title": "İstanbul'da güzel bir gün", "id": "42"},
            "İstanbul'da güzel bir gün - 42",
        ),
    ],
)
def test_build_auto_filename_stem_regression_cases(info: dict[str, object], expected: str) -> None:
    assert build_auto_filename_stem(info) == expected


def test_build_auto_filename_stem_appends_the_id_on_every_service() -> None:
    """The id is always appended, so files stay unique and traceable."""
    assert build_auto_filename_stem({"title": "Big Buck Bunny", "id": "aqz-KE-bpKQ"}) == (
        "Big Buck Bunny - aqz-KE-bpKQ"
    )


@pytest.mark.parametrize(
    "title",
    ["Clip 777 highlights", "Highlights 777", "777 opener"],
)
def test_build_auto_filename_stem_does_not_duplicate_the_id(title: str) -> None:
    stem = build_auto_filename_stem({"title": title, "id": "777"})
    assert stem.count("777") == 1


def test_build_auto_filename_stem_ignores_an_id_that_is_only_a_substring() -> None:
    """'77' inside '7777' is not the id, so the id still gets appended."""
    assert build_auto_filename_stem({"title": "Route 7777", "id": "77"}) == "Route 7777 - 77"


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        # 1. uploader + id
        ({"title": "https://t.co/foo", "uploader": "Trendmkjt", "id": "555"}, "Trendmkjt - 555"),
        # 2. service name + id
        ({"title": "", "extractor_key": "Twitter", "id": "10"}, "Twitter - 10"),
        # 3. id alone
        ({"title": "\U0001f525", "id": "11"}, "11"),
        # 4. last-resort literal when there is no id at all
        ({"title": "https://t.co/x"}, "media"),
        ({}, "media"),
    ],
)
def test_build_auto_filename_stem_fallback_chain(info: dict[str, object], expected: str) -> None:
    assert build_auto_filename_stem(info) == expected


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"title": ""},
        {"title": "https://t.co/x", "uploader": "\U0001f525"},
        {"title": None, "id": None},
    ],
)
def test_build_auto_filename_stem_is_never_empty(info: dict[str, object]) -> None:
    assert build_auto_filename_stem(info).strip() != ""


def test_auto_template_falls_back_if_the_field_is_missing() -> None:
    """The comma syntax keeps a missing field from rendering as literal 'NA'."""
    assert AUTO_OUTPUT_TEMPLATE.startswith(f"%({AUTO_NAME_FIELD},")
    assert "title" in AUTO_OUTPUT_TEMPLATE
    assert AUTO_OUTPUT_TEMPLATE.endswith(".%(ext)s")
