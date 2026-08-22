"""Localhost boundary guards.

Loopback binding alone is not a boundary: any page the user visits can try to
reach the port, and DNS rebinding can point a hostile domain at it. These pin
each defence separately so no single one has to be perfect.
"""

from __future__ import annotations

import pytest

from media_downloader.web.security import (
    SECURITY_HEADERS,
    TOKEN_PLACEHOLDER,
    RequestGuard,
    generate_token,
)

PORT = 8765


@pytest.fixture
def guard() -> RequestGuard:
    return RequestGuard(token="s3cret-token", port=PORT)


def test_tokens_are_long_random_and_unique() -> None:
    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8765", "localhost:8765", "127.0.0.1", "LOCALHOST:8765", "[::1]:8765"],
)
def test_loopback_hosts_are_accepted(guard: RequestGuard, host: str) -> None:
    assert guard.check_host(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.test:8765",
        # The DNS-rebinding case: resolves to loopback, but says otherwise.
        "attacker.example.com:8765",
        "192.168.1.10:8765",
        "127.0.0.1.evil.test",
    ],
)
def test_non_loopback_hosts_are_rejected(guard: RequestGuard, host: str | None) -> None:
    assert not guard.check_host(host)


def test_a_missing_host_header_is_rejected(guard: RequestGuard) -> None:
    """Accepting a missing Host would reopen the rebinding hole."""
    assert not guard.check_host(None)


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:8765", "http://localhost:8765"])
def test_our_own_origin_and_absence_are_allowed(guard: RequestGuard, origin: str | None) -> None:
    assert guard.check_origin(origin)


@pytest.mark.parametrize(
    "origin",
    ["http://evil.test", "https://127.0.0.1:8765", "http://127.0.0.1:9999", "null"],
)
def test_foreign_origins_are_rejected(guard: RequestGuard, origin: str) -> None:
    assert not guard.check_origin(origin)


def test_the_token_must_match_exactly(guard: RequestGuard) -> None:
    assert guard.check_token("s3cret-token")
    for wrong in [None, "", "s3cret-toke", "s3cret-tokenn", "S3CRET-TOKEN"]:
        assert not guard.check_token(wrong)


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", " APPLICATION/JSON "],
)
def test_json_content_type_is_required_for_writes(guard: RequestGuard, content_type: str) -> None:
    assert guard.check_content_type(content_type)


@pytest.mark.parametrize(
    "content_type",
    [None, "", "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
def test_cors_simple_request_types_are_rejected(
    guard: RequestGuard, content_type: str | None
) -> None:
    """These are the types a cross-origin form POST can send without preflight."""
    assert not guard.check_content_type(content_type)


def test_security_headers_cover_the_basics() -> None:
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in SECURITY_HEADERS["Content-Security-Policy"]
    assert SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"
    # No CORS header may ever be declared here.
    assert not any(key.lower().startswith("access-control") for key in SECURITY_HEADERS)


def test_the_placeholder_is_distinctive() -> None:
    assert TOKEN_PLACEHOLDER not in generate_token()
