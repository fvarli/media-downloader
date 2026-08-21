"""Output directory resolution, template validation and automatic naming.

Two separate concerns live here:

* *Safety* -- making sure a user-supplied ``--filename`` template cannot escape
  the output directory. Character-level filename sanitisation is still left to
  yt-dlp, which applies the strict Windows ruleset on every platform (see
  :mod:`media_downloader.options`).
* *Readability* -- turning raw upstream metadata into a filename a human would
  choose. Social platforms routinely put URLs and emoji in the title, and
  yt-dlp's sanitiser transliterates rather than removes them: a title
  containing ``https://t.co/x`` keeps every character, with the colon swapped
  for FULLWIDTH COLON and each slash for BIG SOLIDUS. The cleaning below
  therefore happens on the title *text*, before yt-dlp ever sees it.

Everything in this module is pure, which is what makes the naming policy
straightforward to unit-test.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from media_downloader.errors import OutputError

# Kept for anyone who wants the pre-0.1.1 naming back via --filename.
DEFAULT_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

# Used when the user supplies no --filename. The commas are yt-dlp's field
# fallback syntax: if _md_name is somehow absent the template degrades to the
# raw title, then to the id, rather than rendering the literal string "NA".
AUTO_OUTPUT_TEMPLATE = "%(_md_name,title,id)s.%(ext)s"

# Info-dict key the automatic name is injected under. Deliberately internal:
# the leading underscore keeps it out of yt-dlp's user-facing JSON output, and
# it is not documented as a template field, so it stays free to change.
AUTO_NAME_FIELD = "_md_name"

# Last-resort stem, used only when metadata yields nothing usable at all.
FALLBACK_STEM = "media"

_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")

# --- automatic title cleaning -------------------------------------------

# Explicit schemes, plus bare host/path forms such as "t.co/YF86pOpbhn".
# The bare form requires a slash so that "video.mp4" and "Dr. Who" are safe.
_URL_PATTERN = re.compile(
    r"""(?:
        (?:https?|ftp)://\S+          # scheme-qualified
      | www\.\S+                      # www.example.com/...
      | \b[\w-]+(?:\.[\w-]+)+/\S*     # host.tld/path, e.g. t.co/abc
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Characters no filesystem should have to carry. These cannot be selected by
# Unicode category: ":" and '"' are Po, while "<", ">" and "|" are Sm -- and
# removing all of Sm would also delete harmless "+" and "=".
_FORBIDDEN_CHARS = frozenset(':/\\*?"<>|')

# Categories safe to drop wholesale. So covers emoji and decorative symbols
# (fire, football, (c), degree); Cf covers zero-width joiners and bidi marks;
# Co/Cs/Cn are private-use, surrogates and unassigned.
_DROPPED_CATEGORIES = frozenset({"So", "Cf", "Co", "Cs", "Cn"})

# Emoji presentation selectors. Category Mn, which is *not* dropped wholesale
# because Mn also holds legitimate combining marks for many scripts.
_VARIATION_SELECTORS = frozenset(chr(cp) for cp in range(0xFE00, 0xFE10))

# Escaped so the hyphen is literal wherever this lands inside a character class.
_DASHES = "\\-\u2013\u2014"
# Two or more dashes, optionally spaced, collapse to one. A single hyphen is
# left alone so "well-known" survives.
_DASH_RUN = re.compile(rf"[{_DASHES}](?:\s*[{_DASHES}])+")
_WHITESPACE_RUN = re.compile(r"\s+")
# Separator debris left at either end once URLs and symbols are gone.
_EDGE_SEPARATORS = re.compile(rf"^[\s{_DASHES}_\u00b7\u2022|]+|[\s{_DASHES}_\u00b7\u2022|]+$")


def _strip_unwanted_characters(text: str) -> str:
    """Drop emoji, decorative symbols and filesystem-hostile characters.

    Forbidden characters become a space rather than vanishing, so that
    ``Trend:video`` does not fuse into ``Trendvideo``.
    """
    out: list[str] = []
    for char in text:
        if char in _FORBIDDEN_CHARS:
            out.append(" ")
        elif char in _VARIATION_SELECTORS or unicodedata.category(char) in _DROPPED_CATEGORIES:
            continue
        elif char.isprintable() or char.isspace():
            out.append(char)
    return "".join(out)


def clean_auto_title(title: str | None) -> str:
    """Turn a raw upstream title into readable filename text.

    Removes URLs, emoji and characters that make ugly filenames, then tidies
    the separator and whitespace debris that removal leaves behind. Letters are
    never transliterated, so Turkish and other non-ASCII scripts survive
    intact; the result may legitimately be an empty string if the title
    contained nothing but a URL.
    """
    if not title:
        return ""

    text = _URL_PATTERN.sub(" ", title)
    text = _strip_unwanted_characters(text)
    text = _DASH_RUN.sub("-", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    # Re-run after whitespace collapsing, which can expose a new dash run
    # such as "a - - b" -> "a - - b" -> "a - b".
    text = _DASH_RUN.sub("-", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return _EDGE_SEPARATORS.sub("", text).strip()


def _contains_id(title: str, media_id: str) -> bool:
    """True when ``media_id`` already appears in ``title`` as its own token."""
    return re.search(rf"(?<![\w]){re.escape(media_id)}(?![\w])", title) is not None


def build_auto_filename_stem(info: Mapping[str, Any]) -> str:
    """Build the automatic filename stem (no extension) from yt-dlp metadata.

    The shape is ``<clean title> - <id>``. The id is always appended, so files
    stay unique and traceable back to their source, unless the cleaned title
    already contains it. When cleaning leaves nothing usable, the uploader and
    then the service name stand in for the title. The result is never empty.
    """
    media_id = str(info.get("id") or "").strip()
    title = clean_auto_title(info.get("title"))

    if not title:
        for key in ("uploader", "channel", "creator", "uploader_id"):
            candidate = clean_auto_title(info.get(key))
            if candidate:
                title = candidate
                break

    if not title:
        title = clean_auto_title(info.get("extractor_key") or info.get("extractor"))

    if not media_id:
        return title or FALLBACK_STEM
    if not title:
        return media_id
    if _contains_id(title, media_id):
        return title
    return f"{title} - {media_id}"


def resolve_output_dir(raw: str | Path) -> Path:
    """Expand and absolutise an output directory without creating it.

    ``~`` is expanded via :meth:`pathlib.Path.expanduser`, which works on
    Linux, macOS and Windows alike.
    """
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise OutputError(f"Invalid output directory '{raw}': {exc}") from exc


def ensure_output_dir(path: Path) -> Path:
    """Create ``path`` (including parents) and confirm it is a writable dir."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(
            f"Could not create the output directory '{path}': {exc}",
            hint="Choose a different location with --output.",
        ) from exc

    if not path.is_dir():
        raise OutputError(f"The output path '{path}' exists but is not a directory.")

    return path


def validate_filename_template(template: str) -> str:
    """Validate a yt-dlp output template supplied via ``--filename``.

    The template must name a file *inside* the output directory, so path
    separators, absolute paths, Windows drive letters, UNC prefixes and
    parent-directory hops are all rejected. Rules are applied identically on
    every OS, so a template that works on Linux also works on Windows.

    Raises:
        OutputError: if the template could escape the output directory.
    """
    candidate = template.strip()
    if not candidate:
        raise OutputError("The filename template is empty.")

    if "\x00" in candidate or any(ord(char) < 0x20 for char in candidate):
        raise OutputError("The filename template contains control characters.")

    if _DRIVE_OR_UNC.match(candidate):
        raise OutputError(
            f"The filename template '{candidate}' must be relative, not an absolute path.",
            hint="Use --output to choose the directory and --filename for the name only.",
        )

    # Check both separator conventions regardless of host OS.
    if "/" in candidate or "\\" in candidate:
        raise OutputError(
            f"The filename template '{candidate}' must not contain path separators.",
            hint="Use --output to choose the directory and --filename for the name only.",
        )

    if candidate in {".", ".."} or PureWindowsPath(candidate).is_absolute():
        raise OutputError(f"The filename template '{candidate}' is not a valid file name.")

    return candidate
