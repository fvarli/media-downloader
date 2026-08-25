"""Smoke-test a built artifact, on the runner that produced it.

This exercises the *packaged executable*, not the source tree: the whole point
is to catch things that only break once frozen -- missing data files, resources
loaded by path instead of importlib, an interpreter that still needs the build
virtual environment.

Deliberately structural and deterministic. Nothing here touches the network, so
a transient failure at YouTube can never make a good artifact look broken.

Two modes, because a release-shaped build is not the one CI can read stdout
from:

* ``console`` -- the validation build. Standard output is readable, so the CLI
  banner, help text and startup lines are checked directly.
* ``windowed`` -- the release-shaped macOS ``.app`` and Windows ``.exe``. There
  is no console to read, so *every* observation comes from the file log and the
  HTTP API. Exit codes still work and are still checked; only assertions about
  printed text are skipped.

Each server invocation gets its own app-data root and discovers its address
from that root's log. Nothing is assumed about a port: a stale instance on the
expected one already fooled this script once.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 120
#: How long to wait for a server to write its listening record. Generous: a
#: frozen yt-dlp import on a cold runner is not fast. Bounded: a hang must fail
#: the job, not occupy the runner until the workflow times out.
STARTUP_TIMEOUT = 90
SHUTDOWN_TIMEOUT = 20
#: Bounded so a failure report cannot become the whole build log.
LOG_TAIL_LINES = 40

LISTENING = re.compile(r"server listening on (http://127\.0\.0\.1:\d+)")
ERROR_ID = re.compile(r"error_id=(MD-\d{8}-[A-Z0-9]+)")

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}{f' -- {detail}' if detail and not condition else ''}")
    if not condition:
        failures.append(label)


# -- running the executable ---------------------------------------------


def clean_env(**overrides: str) -> dict[str, str]:
    """A stripped environment.

    If the artifact still needs the build virtual environment, this is where
    that shows up.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "")),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", "/tmp"),
    }
    env.update(overrides)
    return env


def isolated(root: Path) -> dict[str, str]:
    """Point every platform's app-data rule at ``root``.

    Linux reads XDG_DATA_HOME, Windows LOCALAPPDATA, macOS HOME by way of
    ~/Library/Application Support. Overriding all four keeps a run isolated
    wherever it executes, and -- because each invocation gets a fresh root --
    guarantees that anything found in the log belongs to that invocation.
    """
    return {
        "XDG_DATA_HOME": str(root),
        "LOCALAPPDATA": str(root),
        "HOME": str(root),
        "USERPROFILE": str(root),
    }


def run(executable: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [str(executable), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=clean_env(**(env or {})),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timed out"


def http(url: str, token: str | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-MD-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception:
        return 0, b""


# -- one server invocation ----------------------------------------------


def log_path(root: Path) -> Path | None:
    found = list(root.rglob("logs/media-downloader.log"))
    return found[0] if len(found) == 1 else None


def read_log(root: Path) -> str:
    path = log_path(root)
    return path.read_text(errors="replace") if path else ""


class Server:
    """One run of ``--web`` against its own app-data root."""

    def __init__(
        self,
        executable: Path,
        root: Path,
        extra_env: dict[str, str] | None = None,
        args: list[str] | None = None,
    ):
        self.executable = executable
        self.root = root
        self.url = ""
        # No console for the child on Windows, so this is as close to what
        # Explorer does as a spawned process gets. The DEVNULL handles are
        # still valid handles, so sys.stdout is not literally absent here --
        # that case is covered deterministically by the offline tests.
        creation = 0
        if sys.platform == "win32":  # pragma: no cover - Windows only
            creation = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            [str(executable), *(args if args is not None else ["--web"])],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation,
            # Merged, not double-splatted: extra_env may legitimately
            # override one of the isolation keys.
            env=clean_env(**{**isolated(root), **(extra_env or {})}),
        )

    def wait_until_listening(self) -> str:
        """Return the address this invocation bound, from its own log.

        The log is read rather than stdout because a windowed build has none.
        The root is fresh, so a listening record can only have been written by
        this process -- the script cannot latch onto a leftover server, which
        is exactly the mistake that made an earlier version test whatever else
        happened to hold the port.
        """
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if self.process.poll() is not None:
                return ""
            found = LISTENING.findall(read_log(self.root))
            if found:
                self.url = found[-1]
                # And it must be reachable, not merely announced.
                while time.time() < deadline:
                    if http(f"{self.url}/")[0] == 200:
                        return self.url
                    time.sleep(0.2)
                return self.url
            time.sleep(0.2)
        return ""

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.process.kill()
            self.process.wait(timeout=SHUTDOWN_TIMEOUT)


def report_startup_failure(root: Path) -> None:
    """Say why a server never came up, without dumping the whole log."""
    text = read_log(root)
    if not text:
        print(f"  (no log was written under {root})")
        return
    ids = ERROR_ID.findall(text)
    if ids:
        print(f"  error ID: {ids[-1]}")
    lines = text.splitlines()[-LOG_TAIL_LINES:]
    print(f"  last {len(lines)} log line(s):")
    for line in lines:
        print(f"    | {line}")


# -- checks --------------------------------------------------------------


def check_cli(executable: Path, console: bool) -> None:
    print("\nCLI")
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated(Path(tmp))

        code, out = run(executable, "--version", env=env)
        check("--version exits 0", code == 0, out)
        code, out = run(executable, "--help", env=env)
        check("--help exits 0", code == 0, out)

        if console:
            # Only meaningful where there is a console to print to.
            _, version_out = run(executable, "--version", env=env)
            check("--version prints a version", "media-downloader" in version_out, version_out)
            check("--help lists --web", "--web" in out, out)
        else:
            print("  SKIP  printed output (windowed build has no console)")

        code, _ = run(executable, "not-a-url", env=env)
        check("malformed URL exits 3", code == 3, f"got {code}")
        if console:
            # A console build keeps the command-line contract: no arguments
            # means a usage message somebody can actually read.
            code, _ = run(executable, env=env)
            check("bare invocation exits 2", code == 2, f"got {code}")
        else:
            # A windowed build must NOT do that. A double-click passes no
            # arguments, and exiting 2 with no console to print to is exactly
            # how this looked like nothing happening on a real desktop. The
            # positive case -- that it serves -- is checked in check_web.
            print("  SKIP  bare invocation (windowed: it starts the interface, see Web UI)")
        code, _ = run(executable, "https://example.com/x", "--web", env=env)
        check("URL with --web exits 2", code == 2, f"got {code}")


def check_resources(artifact: Path) -> None:
    """Look for bundled files anywhere in the artifact.

    Searched rather than addressed by path because the layouts differ: a plain
    onedir build keeps everything under _internal, while a macOS .app spreads
    it across Contents/. What matters is that the files shipped.
    """
    print("\nPackaged resources")
    for name in ("index.html", "app.css", "app.js"):
        found = list(artifact.rglob(f"web/static/{name}"))
        check(f"static/{name} bundled", len(found) >= 1)
    ejs = list(artifact.rglob("yt_dlp_ejs/**/*.js"))
    check("yt-dlp-ejs JavaScript bundled", len(ejs) >= 1, f"found {len(ejs)}")

    bundled = [
        p.name
        for p in artifact.rglob("*")
        if p.is_file() and p.stem.lower() in {"ffmpeg", "ffprobe", "deno"}
    ]
    check("no FFmpeg or Deno bundled", not bundled, str(bundled))


#: Things a support report must never carry. A report is a file a
#: non-technical user emails to a stranger, so every one of these is a real
#: leak -- and the developer noise is what made an earlier report useless.
FORBIDDEN_IN_REPORT = (
    "X-MD-Token",
    "Authorization",
    "Cookie",
    "pytest",
    "/runner/",
    "runneradmin",
    "site-packages",
    "GITHUB_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
)


def check_report_hygiene(report: str, token: str) -> None:
    """Assert the report carries neither secrets nor developer noise.

    This cannot prove the report from somebody's real desktop is clean -- only
    that run can. What it does is make a regression loud, having already been
    the way a real report turned out to be 120 lines of pytest debris.
    """
    lowered = report.lower()
    for needle in FORBIDDEN_IN_REPORT:
        check(f"report excludes {needle}", needle.lower() not in lowered, needle)
    # A URL's query and fragment are where credentials and tokens live, so the
    # report redacts them rather than trusting that none were present.
    check("report leaks no URL query string", "?v=" not in report and "&token" not in lowered)
    if token:
        check("report excludes the session token verbatim", token not in report)


def check_web(executable: Path, *, console: bool) -> None:
    print("\nWeb UI")
    # This is the heart of the Windows regression. A windowed build is started
    # exactly as a double-click starts it -- with no arguments at all -- rather
    # than with an explicit --web that no user ever types. Asserting the old
    # behaviour here is what let a completely dead artifact ship green.
    launch_args = ["--web"] if console else []
    print(f"  launching as: {' '.join([executable.name, *launch_args])}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # The self-test plants one known error so its ID can be looked for in
        # both the log and the API. Both switches are internal mechanisms, off
        # by default. No browser: a windowed build that cannot open one falls
        # back to a modal dialog, and a modal dialog on an unattended runner
        # waits for a click that never comes.
        server = Server(
            executable,
            root,
            {"MD_DIAGNOSTIC_SELFTEST": "1", "MD_NO_BROWSER": "1"},
            args=launch_args,
        )
        url = ""
        try:
            url = server.wait_until_listening()
            check("server started and bound loopback", url.startswith("http://127.0.0.1:"), url)
            if not url:
                report_startup_failure(root)
                return

            page = http(f"{url}/")[1].decode(errors="replace")
            check("index.html served", "Media Downloader" in page)
            token = ""
            for line in page.splitlines():
                if 'name="md-token"' in line:
                    token = line.split('content="')[1].split('"')[0]
            check("session token present", bool(token))

            check("app.css served", http(f"{url}/static/app.css")[0] == 200)
            check("app.js served", http(f"{url}/static/app.js")[0] == 200)
            check("api requires the token", http(f"{url}/api/config")[0] == 403)

            status, body = http(f"{url}/api/config", token)
            check("/api/config returns JSON", status == 200)
            if status == 200:
                config = json.loads(body)
                check("config reports a version", bool(config.get("version")))
                # A packaged build must not quietly lose the compatibility
                # policy: universal is what stops an MP4 full of VP9 being
                # handed to somebody as a finished download.
                modes = config.get("compatibility_choices") or []
                check(
                    "both compatibility modes offered",
                    set(modes) == {"universal", "original"},
                    str(modes),
                )
                check(
                    "universal is the default",
                    config.get("default_compatibility") == "universal",
                    str(config.get("default_compatibility")),
                )

            status, body = http(f"{url}/api/tools", token)
            check("/api/tools returns JSON", status == 200)
            if status == 200:
                tools = {t["tool"]: t for t in json.loads(body)["tools"]}
                check("both tools reported", set(tools) == {"ffmpeg", "deno"})

            status, body = http(f"{url}/api/diagnostics", token)
            check("/api/diagnostics returns a report", status == 200)
            report = json.loads(body)["report"] if status == 200 else ""
            check("report has content", "Media Downloader diagnostics" in report)
            check("report excludes the session token", bool(token) and token not in report)
            check_report_hygiene(report, token)

            # -- error-ID correlation -----------------------------------
            # The only way to check this on a build with no stdout: plant a
            # known error, then find the same ID in the log and in what the
            # application reports back.
            log_ids = ERROR_ID.findall(read_log(root))
            check("controlled error recorded in the log", bool(log_ids), "none found")
            if log_ids:
                check(
                    "the same error ID is reported by the API", log_ids[-1] in report, log_ids[-1]
                )
        finally:
            server.stop()

        check("server exited cleanly", server.process.poll() is not None)
        check("port released", not url or http(f"{url}/")[0] == 0)

        # -- diagnostics on disk -----------------------------------------
        print("\nDiagnostics")
        path = log_path(root)
        check("log file written to app data", path is not None, str(list(root.rglob("*.log"))))
        if path is not None:
            text = path.read_text(errors="replace")
            check("startup was logged", "startup version=" in text)
            check(
                f"the launch was recorded with {len(launch_args)} argument(s)",
                f"args={len(launch_args)}" in text,
                "no launch record",
            )
            check("server bind was logged", bool(LISTENING.search(text)))
            check("exactly one bind record for this run", len(LISTENING.findall(text)) == 1)
            check("log contains no session token", "X-MD-Token" not in text)


def resolve_executable(artifact: Path) -> Path:
    """Find the executable, whatever shape the artifact is.

    A windowed macOS build is a .app, whose executable lives several levels
    down; everything else is a plain onedir directory.
    """
    name = "media-downloader.exe" if os.name == "nt" else "media-downloader"
    direct = artifact / name
    if direct.is_file():
        return direct
    if artifact.suffix == ".app":
        return artifact / "Contents" / "MacOS" / name
    apps = sorted(artifact.glob("*.app"))
    if apps:
        return apps[0] / "Contents" / "MacOS" / name
    return direct


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--mode",
        choices=("console", "windowed"),
        default="console",
        help="windowed skips assertions about printed text; nothing else differs",
    )
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    executable = resolve_executable(artifact)

    print(f"Artifact: {artifact}")
    print(f"Mode:     {args.mode}")
    print(f"Executable: {executable}")
    check("executable exists", executable.is_file())
    if not executable.is_file():
        return 1

    check_cli(executable, console=args.mode == "console")
    check_resources(artifact)
    check_web(executable, console=args.mode == "console")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All frozen-artifact checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
