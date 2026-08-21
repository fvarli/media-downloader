"""Detection of the JavaScript runtime yt-dlp uses for YouTube challenges.

yt-dlp solves YouTube's JavaScript challenges through ``yt-dlp-ejs``, which
ships the JavaScript itself but still needs a runtime to execute it. yt-dlp
supports Deno, Node and Bun, but **only enables Deno by default** -- an
installed Node is ignored unless it is explicitly enabled.

This module detects a usable runtime and produces the ``js_runtimes`` value
that turns it on. Everything beyond that -- fetching the challenge, running the
JavaScript, interpreting the result -- stays entirely inside yt-dlp; this
project implements no JavaScript or challenge handling of its own.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import Any

# Detection order matches yt-dlp's own preference. Deno first because it is the
# only runtime yt-dlp enables without being asked.
KNOWN_RUNTIMES: tuple[str, ...] = ("deno", "node", "bun")

# Runtimes yt-dlp enables on its own, without a js_runtimes override.
DEFAULT_ENABLED_RUNTIMES: frozenset[str] = frozenset({"deno"})

JS_RUNTIME_GUIDANCE = (
    "No JavaScript runtime was found. yt-dlp needs one to solve YouTube's "
    "JavaScript challenges, and without it some YouTube downloads may fail or "
    "be limited to lower-quality formats. Install this project's optional "
    'extra with: pip install -e ".[js]" -- or install Deno, Node.js or Bun '
    "system-wide so it is available on your PATH."
)


@dataclass(frozen=True)
class JSRuntimeStatus:
    """Which JavaScript runtime, if any, is reachable."""

    name: str | None
    path: str | None = None
    from_package: bool = False

    @property
    def available(self) -> bool:
        return self.name is not None

    @property
    def needs_explicit_enabling(self) -> bool:
        """True when yt-dlp would ignore this runtime unless told to use it."""
        return self.name is not None and self.name not in DEFAULT_ENABLED_RUNTIMES


def detect_js_runtime() -> JSRuntimeStatus:
    """Find a JavaScript runtime yt-dlp can use.

    The PATH is searched first via :func:`shutil.which` (which honours
    ``PATHEXT`` on Windows), then the pip-installable ``deno`` package is
    probed. Because detection reflects the PATH of *this* process, a version
    manager such as nvm that only exports ``node`` in interactive shells is
    correctly reported as unavailable rather than assumed present.
    """
    for name in KNOWN_RUNTIMES:
        found = shutil.which(name)
        if found:
            return JSRuntimeStatus(name=name, path=found)

    if importlib.util.find_spec("deno") is not None:
        return JSRuntimeStatus(name="deno", from_package=True)

    return JSRuntimeStatus(name=None)


def js_runtimes_option(status: JSRuntimeStatus) -> dict[str, dict[str, Any]] | None:
    """Build yt-dlp's ``js_runtimes`` parameter, or ``None`` to leave it alone.

    Returning ``None`` keeps yt-dlp's own default (Deno only). A dictionary is
    returned only when a non-default runtime was found, so that an installed
    Node or Bun is actually used instead of being silently ignored. Deno stays
    enabled alongside it so a later Deno install keeps working.
    """
    if status.name is None or not status.needs_explicit_enabling:
        return None

    enabled: dict[str, dict[str, Any]] = {name: {} for name in DEFAULT_ENABLED_RUNTIMES}
    enabled[status.name] = {}
    return enabled
