"""Optional tools this application can install for the user on request.

FFmpeg and a JavaScript runtime make media-downloader work better, but neither
is a Python package and neither can be pip-installed. Rather than telling a
non-technical user to install them by hand, the application can fetch a pinned,
checksum-verified copy into its own private directory -- but only when the user
explicitly asks for it.

The rules are deliberately strict and are enforced in code, not by convention:

* only URLs from the in-repo manifest are ever fetched, never anything supplied
  by the browser or a user;
* HTTPS only;
* the SHA-256 is verified before the file is unpacked, and nothing is ever
  executed before it verifies;
* archives are unpacked member by member with traversal rejected;
* an installation is promoted into its final location atomically, so a partial
  or failed install can never leave a half-trusted executable behind;
* nothing is written outside the application's own data directory, ``PATH`` is
  never modified, and no step requires administrator rights.

Discovery and installation are separate: looking for a tool never downloads one.
"""

from media_downloader.errors import ToolInstallError
from media_downloader.tools.manager import (
    ToolManager,
    ToolState,
    ToolStatus,
)
from media_downloader.tools.manifest import (
    ToolSpec,
    available_tools,
    lookup,
)

__all__ = [
    "ToolInstallError",
    "ToolManager",
    "ToolSpec",
    "ToolState",
    "ToolStatus",
    "available_tools",
    "lookup",
]
