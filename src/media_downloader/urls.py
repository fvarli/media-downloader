"""URL validation and supported-service detection.

Only ``http`` and ``https`` URLs are accepted. Anything else -- ``file://``,
``javascript:``, bare paths, strings containing control characters -- is
rejected before it ever reaches yt-dlp.

Service detection is informational: the four target services are recognised by
host so the CLI can name them, but any other public http(s) URL is still handed
to yt-dlp, which supports well over a thousand sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from media_downloader.errors import InvalidURLError

ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class Service:
    """A media service this project explicitly targets and tests against."""

    key: str
    name: str
    hosts: frozenset[str]


SERVICES: tuple[Service, ...] = (
    Service(
        key="youtube",
        name="YouTube",
        hosts=frozenset(
            {
                "youtube.com",
                "youtu.be",
                "youtube-nocookie.com",
                "music.youtube.com",
            }
        ),
    ),
    Service(
        key="instagram",
        name="Instagram",
        hosts=frozenset({"instagram.com", "instagr.am", "ddinstagram.com"}),
    ),
    Service(
        key="tiktok",
        name="TikTok",
        hosts=frozenset({"tiktok.com"}),
    ),
    Service(
        key="twitter",
        name="X / Twitter",
        hosts=frozenset({"x.com", "twitter.com"}),
    ),
)

SUPPORTED_SERVICE_NAMES: tuple[str, ...] = tuple(service.name for service in SERVICES)


def normalize_host(netloc: str) -> str:
    """Reduce a URL netloc to a bare lowercase hostname.

    Strips any ``user:password@`` prefix, the ``:port`` suffix, IPv6 brackets
    and a leading ``www.``.
    """
    host = netloc.rpartition("@")[2]
    # IPv6 literals are bracketed, so the port is stripped differently there.
    host = host.partition("]")[0].lstrip("[") if host.startswith("[") else host.partition(":")[0]
    host = host.strip().lower().rstrip(".")
    return host.removeprefix("www.")


def _is_host_or_subdomain(host: str, candidate: str) -> bool:
    """True when ``host`` equals ``candidate`` or is a subdomain of it."""
    return host == candidate or host.endswith(f".{candidate}")


def detect_service(url: str) -> Service | None:
    """Return the explicitly supported :class:`Service` for ``url``, if any.

    Returns ``None`` for every other host; that is not an error, it just means
    the URL falls outside the set of services this project targets directly.
    """
    host = normalize_host(urlsplit(url).netloc)
    for service in SERVICES:
        if any(_is_host_or_subdomain(host, known) for known in service.hosts):
            return service
    return None


def validate_url(raw: str) -> str:
    """Validate ``raw`` and return it cleaned of surrounding whitespace.

    Raises:
        InvalidURLError: if the value is empty, contains control characters or
            is not an absolute ``http``/``https`` URL with a hostname.
    """
    url = raw.strip()
    if not url:
        raise InvalidURLError("No URL was provided.")

    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise InvalidURLError(
            "The URL contains whitespace or control characters.",
            hint="Wrap the URL in quotes so your shell passes it through unchanged.",
        )

    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise InvalidURLError(f"The URL could not be parsed: {exc}") from exc

    if not parts.scheme:
        raise InvalidURLError(
            f"'{url}' is not a complete URL.",
            hint="Include the scheme, for example https://www.youtube.com/watch?v=...",
        )

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURLError(
            f"Unsupported URL scheme '{parts.scheme}'.",
            hint="Only http:// and https:// URLs are accepted.",
        )

    if not normalize_host(parts.netloc):
        raise InvalidURLError(
            f"'{url}' has no hostname.",
            hint="Include the scheme, for example https://www.youtube.com/watch?v=...",
        )

    return url
