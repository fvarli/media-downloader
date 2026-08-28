"""HTTPS trust for managed downloads.

These exist because of a real failure. On a Windows machine the packaged
application could not install FFmpeg or Deno -- CERTIFICATE_VERIFY_FAILED,
"unable to get local issuer certificate" -- while yt-dlp reached YouTube from
the same process on the same machine. yt-dlp asks for certifi's bundle; our
downloader asked for nothing and got whatever the platform happened to have.

CI never caught it, because its runners have complete certificate stores. So
the tests that matter most here assert the *configuration* rather than the
outcome: those hold regardless of how healthy the machine running them is.
"""

from __future__ import annotations

import http.server
import inspect
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from media_downloader.errors import ToolInstallError
from media_downloader.tools import trust


def _subjects(context: ssl.SSLContext) -> set[tuple[Any, ...]]:
    return {tuple(tuple(part) for part in cert["subject"]) for cert in context.get_ca_certs()}


# -- verification is not negotiable --------------------------------------


def test_hostname_verification_is_on() -> None:
    assert trust.create_https_context().check_hostname is True


def test_certificates_are_required() -> None:
    assert trust.create_https_context().verify_mode is ssl.CERT_REQUIRED


def test_nothing_in_the_module_disables_verification() -> None:
    """The forbidden shapes, checked against the source itself.

    A checksum afterwards does not justify an unverified connection, and this
    is the one place where a well-meaning "just make it work" change would be
    both easy and catastrophic.
    """
    source = inspect.getsource(trust)
    # The module docstring names these deliberately, to say they are absent.
    # Scanning it would make the test pass or fail on prose.
    docstring = trust.__doc__ or ""
    code = source.replace(docstring, "")
    for forbidden in (
        "_create_unverified_context",
        "CERT_NONE",
        "check_hostname = False",
        "verify_mode = ssl.CERT_OPTIONAL",
    ):
        assert forbidden not in code, forbidden


# -- additive trust ------------------------------------------------------


def test_certifi_is_added_to_platform_trust_not_substituted_for_it() -> None:
    """The whole fix in one assertion.

    certifi alone would have repaired Windows and broken every machine whose
    administrator installed a private root -- the ordinary case behind a
    TLS-inspecting proxy.
    """
    certifi = pytest.importorskip("certifi")

    ours = _subjects(trust.create_https_context())
    platform_only = _subjects(ssl.create_default_context())
    certifi_only = _subjects(ssl.create_default_context(cafile=certifi.where()))

    assert platform_only <= ours, "platform roots were dropped"
    assert certifi_only <= ours, "certifi roots were not added"


def test_the_context_trusts_exactly_the_union_of_the_two_sources() -> None:
    """Not "more than either" -- that is only true where they differ.

    On macOS the platform trust *is* certifi, because the python.org installer
    points OpenSSL at that same bundle, so the two sets are identical there and
    a strict-growth assertion would fail on a perfectly correct build. The
    invariant that holds everywhere is that we trust their union: everything
    from both, and nothing beyond them.
    """
    certifi = pytest.importorskip("certifi")

    ours = _subjects(trust.create_https_context())
    platform_only = _subjects(ssl.create_default_context())
    certifi_only = _subjects(ssl.create_default_context(cafile=certifi.where()))

    assert ours == platform_only | certifi_only


def test_the_context_is_never_smaller_than_either_source() -> None:
    certifi = pytest.importorskip("certifi")
    ours = _subjects(trust.create_https_context())
    assert len(ours) >= len(_subjects(ssl.create_default_context()))
    assert len(ours) >= len(_subjects(ssl.create_default_context(cafile=certifi.where())))


def test_the_trust_sources_are_reported_for_diagnostics() -> None:
    sources = trust.trust_sources()
    assert sources.system is True
    assert sources.certifi is True
    assert "certifi" in sources.describe()
    assert "system" in sources.describe()


def test_the_description_carries_no_path() -> None:
    """A support report says what is trusted, never where it lives on disk."""
    described = trust.trust_sources().describe()
    assert "/" not in described and "\\" not in described


# -- failing closed ------------------------------------------------------


def test_a_context_survives_certifi_being_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Losing certifi must fall back to the platform's own trust, which is
    still fully verified -- never to something unverified."""
    monkeypatch.setattr(trust, "_add_certifi", lambda context: (False, None))
    context = trust.create_https_context()
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_a_context_survives_an_unreadable_platform_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows can raise on one malformed entry in its own store."""

    def unreadable() -> ssl.SSLContext:
        raise ssl.SSLError("bad entry in the store")

    monkeypatch.setattr(ssl, "create_default_context", unreadable)
    context = trust.create_https_context()
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert trust._build()[1].system is False


def test_no_trust_at_all_refuses_to_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing to verify against, continuing would mean trusting whatever
    answered. The download does not happen."""
    monkeypatch.setattr(
        trust,
        "_build",
        lambda: (
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            trust.TrustSources(False, False, None, 0),
        ),
    )
    with pytest.raises(ToolInstallError, match="No certificate authorities"):
        trust.create_https_context()


# -- redirects -----------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["http://example.invalid/x.zip", "ftp://example.invalid/x.zip", "file:///etc/passwd"],
)
def test_a_redirect_off_https_is_refused(target: str) -> None:
    handler = trust.HTTPSOnlyRedirectHandler()
    import urllib.request

    request = urllib.request.Request("https://example.invalid/x.zip")
    with pytest.raises(ToolInstallError, match="redirected away from HTTPS"):
        handler.redirect_request(request, None, 302, "Found", {}, target)


def test_a_redirect_that_stays_on_https_is_followed() -> None:
    """Every pinned download redirects at least once, so this must work."""
    import urllib.request

    handler = trust.HTTPSOnlyRedirectHandler()
    request = urllib.request.Request("https://example.invalid/x.zip")
    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://elsewhere.invalid/x.zip"
    )
    assert redirected is not None
    assert redirected.full_url == "https://elsewhere.invalid/x.zip"


def test_a_plain_http_url_is_refused_outright(tmp_path: Path) -> None:
    with pytest.raises(ToolInstallError, match="non-HTTPS"):
        trust.https_fetch("http://example.invalid/x.zip", tmp_path / "out", max_bytes=10)


# -- a real handshake ----------------------------------------------------
#
# Everything above judges configuration. Only a live connection proves that an
# untrusted certificate is actually rejected, so these mint a throwaway one and
# serve it. Skipped where openssl is unavailable to mint it, in the same way
# the media tests skip without FFmpeg.

openssl_required = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="needs openssl to mint a throwaway certificate"
)


def _mint(directory: Path, common_name: str) -> tuple[Path, Path]:
    """A self-signed certificate and its key, valid for ``common_name``."""
    cert, key = directory / f"{common_name}.pem", directory / f"{common_name}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            f"/CN={common_name}",
            "-addext",
            f"subjectAltName=DNS:{common_name}",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return cert, key


class _Server:
    """A one-request HTTPS server using the certificate it is given."""

    def __init__(self, cert: Path, key: Path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = b"payload"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@openssl_required
def test_an_untrusted_certificate_is_rejected(tmp_path: Path) -> None:
    """The real behaviour the whole module exists to guarantee."""
    cert, key = _mint(tmp_path, "localhost")
    server = _Server(cert, key)
    destination = tmp_path / "out.bin"
    try:
        with pytest.raises(ToolInstallError, match="Secure connection verification failed"):
            trust.https_fetch(f"https://localhost:{server.port}/x", destination, max_bytes=1024)
    finally:
        server.close()


@openssl_required
def test_nothing_is_written_when_verification_fails(tmp_path: Path) -> None:
    cert, key = _mint(tmp_path, "localhost")
    server = _Server(cert, key)
    destination = tmp_path / "out.bin"
    try:
        with pytest.raises(ToolInstallError):
            trust.https_fetch(f"https://localhost:{server.port}/x", destination, max_bytes=1024)
    finally:
        server.close()
    assert not destination.exists() or destination.stat().st_size == 0


@openssl_required
def test_a_certificate_signed_by_a_trusted_authority_is_accepted(tmp_path: Path) -> None:
    """The positive case, so the rejection above is not simply "nothing works"."""
    cert, key = _mint(tmp_path, "localhost")
    server = _Server(cert, key)
    try:
        context = ssl.create_default_context(cafile=str(cert))
        with (
            socket.create_connection(("127.0.0.1", server.port), timeout=30) as raw,
            context.wrap_socket(raw, server_hostname="localhost") as tls,
        ):
            assert tls.getpeercert() is not None
    finally:
        server.close()


@openssl_required
def test_a_hostname_mismatch_is_rejected(tmp_path: Path) -> None:
    """Trusting the certificate is not the same as it being for this host."""
    cert, key = _mint(tmp_path, "somewhere-else.invalid")
    server = _Server(cert, key)
    try:
        context = ssl.create_default_context(cafile=str(cert))
        with (
            socket.create_connection(("127.0.0.1", server.port), timeout=30) as raw,
            pytest.raises(ssl.CertificateError),
        ):
            context.wrap_socket(raw, server_hostname="localhost")
    finally:
        server.close()
