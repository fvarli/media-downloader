"""Where HTTPS trust comes from, and the one path every managed download takes.

This module exists because of a real failure. On a Windows machine the packaged
application could not install FFmpeg or Deno:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

and yet yt-dlp, running in the *same process on the same machine*, reached
YouTube perfectly well. That is the whole diagnosis in one observation. It was
never a broken computer: it was two different trust sources inside one program.
yt-dlp asks for certifi's CA bundle explicitly. Our downloader asked for
nothing, so it got urllib's default context -- which on Windows means whatever
roots happen to be sitting in the ROOT store, plus OpenSSL default paths that do
not exist there at all. Windows fills that store lazily, so a machine that has
never needed a particular root simply does not have it, and both of the sites we
download from now chain to roots added only recently.

The fix is additive, and the distinction matters. certifi is *added* to the
platform's own trust, never substituted for it, so a private root that an
administrator installed -- the ordinary case behind a TLS-inspecting proxy --
keeps working, while a machine missing a new public root is repaired.

Nothing here ever relaxes verification. There is no unverified context, no
CERT_NONE, no disabled hostname check, no insecure retry after a failure and no
fall back to plain HTTP. If trust cannot be established the download does not
happen.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from media_downloader.errors import ToolInstallError
from media_downloader.logging_setup import get_logger

logger = get_logger("trust")

#: Long enough for a slow connection to fetch a hundred megabytes, short enough
#: that a hung server does not hold the interface forever.
DOWNLOAD_TIMEOUT_SECONDS = 60

CHUNK_BYTES = 1024 * 256


@dataclass(frozen=True)
class TrustSources:
    """Which certificate authorities the HTTPS layer ended up trusting."""

    system: bool
    certifi: bool
    certifi_version: str | None
    authority_count: int

    @property
    def usable(self) -> bool:
        """False only when neither source could be loaded at all."""
        return self.system or self.certifi

    def describe(self) -> str:
        """One short line for diagnostics. Never a path, never a certificate."""
        parts: list[str] = []
        if self.system:
            parts.append("system")
        if self.certifi:
            parts.append(f"certifi {self.certifi_version}" if self.certifi_version else "certifi")
        return " + ".join(parts) if parts else "none"


def _load_platform_trust() -> tuple[ssl.SSLContext, bool]:
    """The platform's own roots, which is what an enterprise root lives in.

    ``create_default_context`` loads them, and on Windows it can raise on a
    single malformed entry in the store. That must not cost us the connection,
    so the fallback is a context that is still fully verifying and simply has
    no platform roots yet -- certifi is added to it next.
    """
    try:
        return ssl.create_default_context(), True
    except (ssl.SSLError, OSError):
        logger.debug("The platform certificate store could not be read; continuing with certifi.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context, False


def _add_certifi(context: ssl.SSLContext) -> tuple[bool, str | None]:
    """Add Mozilla's roots to ``context``. Added, never substituted."""
    try:
        import certifi

        bundle = Path(certifi.where())
        if not bundle.is_file():
            logger.debug("certifi reported a CA bundle that is not present.")
            return False, None
        context.load_verify_locations(cafile=str(bundle))
        return True, getattr(certifi, "__version__", None)
    except Exception:  # a missing bundle must not break the platform context
        logger.debug("certifi's CA bundle could not be loaded; continuing with platform trust.")
        return False, None


def _build() -> tuple[ssl.SSLContext, TrustSources]:
    context, system = _load_platform_trust()
    certifi_loaded, certifi_version = _add_certifi(context)

    # Restated rather than assumed. These are the two properties that make the
    # connection meaningful, and nothing in this module may leave them off.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    sources = TrustSources(
        system=system,
        certifi=certifi_loaded,
        certifi_version=certifi_version,
        # Informational only. OpenSSL loads a capath lazily, so a low count
        # does not mean verification will fail and must not be treated as an
        # error condition.
        authority_count=len(context.get_ca_certs()),
    )
    return context, sources


def create_https_context() -> ssl.SSLContext:
    """A verified TLS context trusting the platform's roots and certifi's.

    Raises:
        ToolInstallError: if neither source could be loaded, because then there
            is nothing to verify against and continuing would mean trusting
            whatever answered.
    """
    context, sources = _build()
    if not sources.usable:
        raise ToolInstallError(
            "No certificate authorities are available, so the download cannot be verified.",
            hint=(
                "Nothing was downloaded. This usually means the installation is "
                "incomplete or the system certificate store is unreadable."
            ),
        )
    return context


@lru_cache(maxsize=1)
def trust_sources() -> TrustSources:
    """What the HTTPS layer trusts, for diagnostics. Computed once."""
    return _build()[1]


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects only while they stay on HTTPS.

    Checking the URL we were given is not enough on its own: ``urlopen``
    follows redirects, and the default handler is happy to follow one to
    ``http://``. Every download we pin redirects at least once -- GitHub sends
    release assets to a separate host -- so the hop is the normal path, not an
    edge case, and it is unverified by the initial scheme check.

    Fails closed. A download that cannot stay on HTTPS does not happen.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        scheme = urlsplit(newurl).scheme
        if scheme != "https":
            raise ToolInstallError(
                "Refusing a download that redirected away from HTTPS.",
                hint="The download was stopped before anything was written.",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def create_https_opener() -> urllib.request.OpenerDirector:
    """The single opener every managed download goes through."""
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=create_https_context()),
        HTTPSOnlyRedirectHandler,
    )


def _verification_failed(reason: BaseException) -> ToolInstallError:
    """Turn a certificate failure into something a person can act on.

    The raw ``_ssl.c`` text goes to the log, where it is genuinely useful. What
    reaches the interface says what happened and what was done about it, and
    suggests only what the evidence supports -- never that somebody should turn
    off their antivirus or their certificate checking.
    """
    logger.error("TLS verification failed for a managed download: %s", reason)
    return ToolInstallError(
        "Secure connection verification failed.",
        hint=(
            "The download was stopped rather than continue unverified. This "
            "usually means this computer's certificate store is out of date, or "
            "that a network appliance is inspecting HTTPS traffic. Nothing was "
            "downloaded."
        ),
    )


def https_fetch(url: str, destination: Path, *, max_bytes: int) -> None:
    """Fetch ``url`` over verified HTTPS into ``destination``.

    Refuses any non-HTTPS URL even though the manifest is the only caller -- a
    second check costs nothing and means a future manifest typo cannot silently
    downgrade the transport. Redirects are held to the same rule.
    """
    if urlsplit(url).scheme != "https":
        raise ToolInstallError(f"Refusing a non-HTTPS download URL: {url}")

    request = urllib.request.Request(url, headers={"User-Agent": "media-downloader"})
    opener = create_https_opener()
    written = 0
    try:
        with (
            opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            destination.open("wb") as out,
        ):
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ToolInstallError("The download was larger than expected and was stopped.")
                out.write(chunk)
    except ToolInstallError:
        raise
    except ssl.SSLCertVerificationError as exc:
        raise _verification_failed(exc) from exc
    except urllib.error.URLError as exc:
        # urlopen wraps the certificate error, so the useful one is inside.
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise _verification_failed(exc.reason) from exc
        raise ToolInstallError(f"The download failed: {exc}") from exc
    except Exception as exc:
        raise ToolInstallError(f"The download failed: {exc}") from exc
