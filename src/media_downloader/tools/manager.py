"""Installing and locating managed tools.

Two responsibilities that are deliberately kept apart:

* **discovery** -- where is this tool, if anywhere? Pure lookup; it never
  downloads and never touches the network.
* **installation** -- fetch, verify, unpack and promote a pinned copy. Only
  ever reached because the user explicitly asked for it.

Keeping them separate is what guarantees that merely starting the application,
or checking whether FFmpeg exists, can never pull 113 MB down someone's
connection.
"""

from __future__ import annotations

import os
import platform as platform_module
import shutil
import stat
import tempfile
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from media_downloader.errors import MediaDownloaderError
from media_downloader.logging_setup import get_logger
from media_downloader.paths import ensure_dir, tool_install_dir, tools_dir
from media_downloader.tools.archive import extract_members
from media_downloader.tools.manifest import ToolSpec, lookup
from media_downloader.tools.verify import verify_sha256
from media_downloader.web.system import current_platform

logger = get_logger("tools")

DOWNLOAD_TIMEOUT_SECONDS = 60
#: Refuse a response wildly larger than the manifest says, so a compromised or
#: redirected source cannot fill the user's disk before the checksum is checked.
SIZE_TOLERANCE = 1.5


class ToolInstallError(MediaDownloaderError):
    """An install could not be completed. Nothing was left behind."""


class ToolState(str, Enum):
    """Where a tool is coming from, if anywhere."""

    SYSTEM = "system"
    MANAGED = "managed"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    INSTALLING = "installing"


@dataclass(frozen=True)
class ToolStatus:
    """What the UI needs to decide whether to offer an install."""

    tool: str
    state: ToolState
    version: str | None = None
    path: Path | None = None
    size_bytes: int | None = None
    licence: str | None = None
    source: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.state in {ToolState.SYSTEM, ToolState.MANAGED}

    @property
    def can_install(self) -> bool:
        """True when offering a download would actually achieve something."""
        return self.state is ToolState.MISSING


class Fetcher(Protocol):
    """Downloads a URL to a path. Injected so tests never hit the network."""

    def __call__(self, url: str, destination: Path, *, max_bytes: int) -> None: ...


def https_fetch(url: str, destination: Path, *, max_bytes: int) -> None:
    """Fetch ``url`` over HTTPS into ``destination``.

    Refuses any non-HTTPS URL even though the manifest is the only caller --
    a second check costs nothing and means a future manifest typo cannot
    silently downgrade the transport.
    """
    if urlsplit(url).scheme != "https":
        raise ToolInstallError(f"Refusing a non-HTTPS download URL: {url}")

    request = urllib.request.Request(url, headers={"User-Agent": "media-downloader"})
    written = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            destination.open("wb") as out,
        ):
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ToolInstallError("The download was larger than expected and was stopped.")
                out.write(chunk)
    except ToolInstallError:
        raise
    except Exception as exc:
        raise ToolInstallError(f"The download failed: {exc}") from exc


class ToolManager:
    """Discovers and installs the optional tools."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        fetcher: Fetcher | None = None,
        platform_name: Callable[[], str] | None = None,
        machine: Callable[[], str] | None = None,
    ) -> None:
        self._env = env
        self._fetch: Fetcher = fetcher or https_fetch
        self._platform = platform_name or current_platform
        self._machine = machine or _host_machine

    # -- manifest --------------------------------------------------------

    def spec_for(self, tool: str) -> ToolSpec | None:
        """The pinned spec for this tool on this machine, if we have one."""
        return lookup(tool, self._platform(), self._machine())

    # -- discovery (never downloads) -------------------------------------

    def managed_path(self, tool: str, executable: str) -> Path | None:
        """Path to an already-installed managed executable, if present.

        Only the version the manifest currently pins is considered: an older
        directory left from a previous pin is ignored rather than silently used.
        """
        spec = self.spec_for(tool)
        if spec is None:
            return None
        candidate = tool_install_dir(tool, spec.version, self._env) / executable
        return candidate if candidate.is_file() else None

    def managed_dir(self, tool: str) -> Path | None:
        """Directory holding the installed managed copy, if complete.

        Complete means *every* executable the manifest declares is present --
        an FFmpeg install missing ffprobe is not usable and is not reported.
        """
        spec = self.spec_for(tool)
        if spec is None:
            return None
        directory = tool_install_dir(tool, spec.version, self._env)
        if all((directory / name).is_file() for name in spec.executables):
            return directory
        return None

    def status(self, tool: str, *, system_path: Path | None) -> ToolStatus:
        """Summarise where ``tool`` comes from.

        ``system_path`` is supplied by the existing detection in
        :mod:`media_downloader.ffmpeg` / :mod:`media_downloader.jsruntime`, so
        this module never re-implements PATH lookup.
        """
        spec = self.spec_for(tool)
        if system_path is not None:
            # No version: the manifest describes the copy we would install, not
            # the one already on this machine, and they are frequently not even
            # the same program -- a system Node satisfies the JavaScript runtime
            # requirement that the manifest pins Deno for. Reporting the
            # manifest's number here once produced "system 2.9.5" for Node 22.
            return ToolStatus(tool=tool, state=ToolState.SYSTEM, path=system_path)

        managed = self.managed_dir(tool)
        if managed is not None and spec is not None:
            return ToolStatus(
                tool=tool,
                state=ToolState.MANAGED,
                version=spec.version,
                path=managed,
                licence=spec.licence,
                source=spec.source,
            )

        if spec is None:
            return ToolStatus(tool=tool, state=ToolState.UNSUPPORTED)

        return ToolStatus(
            tool=tool,
            state=ToolState.MISSING,
            version=spec.version,
            size_bytes=spec.size_bytes,
            licence=spec.licence,
            source=spec.source,
        )

    # -- installation (explicit user action only) ------------------------

    def install(self, tool: str) -> Path:
        """Download, verify, unpack and promote the pinned copy of ``tool``.

        Everything happens inside a temporary directory that is removed on any
        failure, so the tools directory only ever gains a complete, verified
        installation.

        Raises:
            ToolInstallError: with a message suitable for showing to a user.
        """
        spec = self.spec_for(tool)
        if spec is None:
            raise ToolInstallError(
                f"{tool} cannot be installed automatically on this system yet.",
                hint="Install it with your system package manager instead.",
            )

        final = tool_install_dir(tool, spec.version, self._env)
        if self.managed_dir(tool) is not None:
            logger.debug("%s %s is already installed", tool, spec.version)
            return final

        ensure_dir(tools_dir(self._env))
        staging = Path(tempfile.mkdtemp(prefix=f"{tool}-", dir=tools_dir(self._env)))
        try:
            archive = staging / Path(urlsplit(spec.url).path).name
            logger.info("Downloading %s %s", tool, spec.version)
            self._fetch(spec.url, archive, max_bytes=int(spec.size_bytes * SIZE_TOLERANCE))

            # Nothing is unpacked, let alone executed, before this passes.
            logger.info("verifying %s checksum", tool)
            verify_sha256(archive, spec.sha256)
            logger.info("%s checksum verified", tool)

            logger.info("extracting %s", tool)
            unpacked = staging / "unpacked"
            unpacked.mkdir()
            written = extract_members(archive, unpacked, spec.members)

            missing = [name for name in spec.executables if name not in written]
            if missing:  # pragma: no cover - extract_members already raises
                raise ToolInstallError(f"{tool} archive was missing: {', '.join(missing)}")

            for name in spec.executables:
                path = written[name]
                if not path.is_file() or path.stat().st_size == 0:
                    raise ToolInstallError(f"{tool} archive produced an unusable {name}.")
                _make_executable(path)

            archive.unlink(missing_ok=True)
            ensure_dir(final.parent)
            # Atomic: the final path either does not exist or is a complete,
            # verified installation. It is never observed half-populated.
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            unpacked.replace(final)
        except MediaDownloaderError:
            raise
        except OSError as exc:
            raise ToolInstallError(f"Installing {tool} failed: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        logger.info("Installed %s %s into %s", tool, spec.version, final)
        return final


@lru_cache(maxsize=1)
def _host_machine() -> str:
    """The machine architecture, worked out once.

    ``platform.machine()`` shells out to ``ver`` on some Windows and Python
    combinations, and discovery constructs a manager on every lookup, so this
    is cached rather than re-derived on each call.
    """
    return platform_module.machine()


def _make_executable(path: Path) -> None:
    """Add the executable bit on POSIX. A no-op on Windows, which has none."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
