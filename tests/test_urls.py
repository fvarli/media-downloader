"""URL validation and service detection."""

from __future__ import annotations

import pytest

from media_downloader.errors import InvalidURLError
from media_downloader.urls import detect_service, normalize_host, validate_url


@pytest.mark.parametrize(
    ("netloc", "expected"),
    [
        ("www.youtube.com", "youtube.com"),
        ("YouTube.COM", "youtube.com"),
        ("m.youtube.com:443", "m.youtube.com"),
        ("user:pass@youtube.com", "youtube.com"),
        ("[2001:db8::1]:8080", "2001:db8::1"),
        ("youtube.com.", "youtube.com"),
    ],
)
def test_normalize_host(netloc: str, expected: str) -> None:
    assert normalize_host(netloc) == expected


@pytest.mark.parametrize(
    ("url", "expected_key"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://m.youtube.com/watch?v=abc", "youtube"),
        ("https://music.youtube.com/watch?v=abc", "youtube"),
        ("https://www.instagram.com/reel/abc/", "instagram"),
        ("https://www.tiktok.com/@user/video/123", "tiktok"),
        ("https://vm.tiktok.com/ABC123/", "tiktok"),
        ("https://vt.tiktok.com/ABC123/", "tiktok"),
        ("https://x.com/user/status/123", "twitter"),
        ("https://twitter.com/user/status/123", "twitter"),
        ("https://mobile.twitter.com/user/status/123", "twitter"),
    ],
)
def test_detect_service_recognises_targets(url: str, expected_key: str) -> None:
    service = detect_service(url)
    assert service is not None
    assert service.key == expected_key


@pytest.mark.parametrize(
    "url",
    ["https://vimeo.com/12345", "https://example.com/video.mp4", "https://notyoutube.com/x"],
)
def test_detect_service_returns_none_for_other_hosts(url: str) -> None:
    assert detect_service(url) is None


def test_detect_service_is_not_fooled_by_suffix_lookalikes() -> None:
    # "evil-youtube.com" must not match "youtube.com".
    assert detect_service("https://evil-youtube.com/watch?v=abc") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "http://example.com/video",
        "  https://x.com/a/status/1  ",
    ],
)
def test_validate_url_accepts_http_and_https(url: str) -> None:
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "youtube.com/watch?v=abc",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.com/file.mp4",
        "data:text/html,<h1>hi</h1>",
        "https://",
        "https://example.com/a b",
        "https://example.com/\x00",
        "https://example.com/x\nrm -rf /",
    ],
)
def test_validate_url_rejects_unsafe_input(url: str) -> None:
    with pytest.raises(InvalidURLError):
        validate_url(url)


def test_invalid_url_error_carries_exit_code() -> None:
    with pytest.raises(InvalidURLError) as excinfo:
        validate_url("file:///etc/passwd")
    assert int(excinfo.value.exit_code) == 3
