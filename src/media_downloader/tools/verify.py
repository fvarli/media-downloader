"""Checksum verification.

Verification is fail-closed: anything that is not provably the expected file is
treated as hostile, and the caller deletes it. Nothing downloaded is executed,
unpacked, or moved into place before this passes.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from media_downloader.errors import MediaDownloaderError

CHUNK_BYTES = 1024 * 1024


class ChecksumError(MediaDownloaderError):
    """A downloaded file did not match its pinned checksum."""


def sha256_file(path: Path) -> str:
    """Hash a file in chunks.

    Streamed rather than read whole: these downloads run to ~113 MB and there is
    no reason to hold that in memory.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Raise unless ``path`` hashes to ``expected``.

    Raises:
        ChecksumError: on any mismatch. The caller is responsible for removing
            the file -- this function never leaves a verdict implicit.
    """
    actual = sha256_file(path)
    if not hmac.compare_digest(actual.lower(), expected.strip().lower()):
        raise ChecksumError(
            "The downloaded file does not match its expected checksum and was discarded.",
            hint=f"expected {expected}, got {actual}",
        )
