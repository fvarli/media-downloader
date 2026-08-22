"""Guards for the loopback HTTP server.

The server only listens on 127.0.0.1, but "localhost only" is not by itself a
security boundary: any page the user visits can try to talk to it, and DNS
rebinding can make a hostile domain resolve to loopback. The defences here are
deliberately layered so that no single one has to be perfect.

1. **Session token.** A random token is minted per process and embedded in the
   page we serve. Every ``/api/`` request must echo it. A remote page cannot
   read it, because we never send CORS headers, so it cannot forge a request
   even if it can reach the port.
2. **Host allowlist.** The ``Host`` header must name loopback, which is what
   defeats DNS rebinding: the rebound request arrives with the attacker's
   hostname.
3. **Origin check.** When an ``Origin`` is present it must be our own.
4. **JSON-only writes.** State-changing requests must be ``application/json``,
   which is not a CORS "simple request", so the browser preflights it and --
   with no CORS headers in the response -- blocks it.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass

#: Header carrying the session token.
TOKEN_HEADER = "X-MD-Token"

#: Placeholder replaced in index.html when the page is served.
TOKEN_PLACEHOLDER = "__MD_TOKEN__"

#: Hostnames that may appear in the Host header.
ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

SECURITY_HEADERS: dict[str, str] = {
    # The UI loads only its own scripts and styles; nothing is fetched
    # off-machine, and no inline script is used.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def generate_token() -> str:
    """Mint a session token. New on every server start."""
    return secrets.token_urlsafe(32)


def _split_host(value: str) -> str:
    """Return the hostname part of a Host header, keeping IPv6 brackets."""
    host = value.strip()
    if host.startswith("["):
        return host.partition("]")[0] + "]"
    return host.partition(":")[0]


@dataclass(frozen=True)
class RequestGuard:
    """Validates incoming requests against this server's identity."""

    token: str
    port: int

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(
            f"http://{host}:{self.port}" for host in ("127.0.0.1", "localhost", "[::1]")
        )

    def check_host(self, host_header: str | None) -> bool:
        """True when the Host header names loopback.

        A missing Host is rejected: every HTTP/1.1 client sends one, and
        accepting its absence would reopen the rebinding hole.
        """
        if not host_header:
            return False
        return _split_host(host_header).lower() in ALLOWED_HOSTNAMES

    def check_origin(self, origin: str | None) -> bool:
        """True when the Origin is ours or absent.

        Absent is legitimate: same-origin GETs and non-CORS navigations send no
        Origin. A *present* but foreign Origin is always rejected.
        """
        if origin is None or origin == "null":
            return origin is None
        return origin.lower() in self.allowed_origins

    def check_token(self, supplied: str | None) -> bool:
        """Constant-time comparison of the session token."""
        if not supplied:
            return False
        return hmac.compare_digest(supplied, self.token)

    def check_content_type(self, content_type: str | None) -> bool:
        """True for application/json, which forces a CORS preflight."""
        if not content_type:
            return False
        return content_type.split(";")[0].strip().lower() == "application/json"
