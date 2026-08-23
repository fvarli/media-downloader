"""Smoke-test a built artifact, on the runner that produced it.

This exercises the *packaged executable*, not the source tree: the whole point
is to catch things that only break once frozen -- missing data files, resources
loaded by path instead of importlib, an interpreter that still needs the build
virtual environment.

Deliberately structural and deterministic. Nothing here touches the network, so
a transient failure at YouTube can never make a good artifact look broken.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 120
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}{f' -- {detail}' if detail and not condition else ''}")
    if not condition:
        failures.append(label)


def run(executable: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run the frozen binary in a clean environment.

    A stripped environment is the point: if the artifact still needs the build
    virtual environment, this is where that shows up.
    """
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "")),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", "/tmp"),
    }
    base.update(env or {})
    try:
        proc = subprocess.run(
            [str(executable), *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=base,
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


def main() -> int:
    artifact = Path(sys.argv[1]).resolve()
    executable = artifact / ("media-downloader.exe" if os.name == "nt" else "media-downloader")

    print(f"Artifact: {artifact}")
    check("executable exists", executable.is_file())
    if not executable.is_file():
        return 1

    # -- CLI ------------------------------------------------------------
    print("\nCLI")
    code, out = run(executable, "--version")
    check("--version exits 0", code == 0, out)
    check("--version prints a version", "media-downloader" in out, out)

    code, out = run(executable, "--help")
    check("--help exits 0", code == 0, out)
    check("--help lists --web", "--web" in out, out)

    code, _ = run(executable, "not-a-url")
    check("malformed URL exits 3", code == 3, f"got {code}")

    code, _ = run(executable)
    check("bare invocation exits 2", code == 2, f"got {code}")

    code, _ = run(executable, "https://example.com/x", "--web")
    check("URL with --web exits 2", code == 2, f"got {code}")

    # -- packaged resources ---------------------------------------------
    print("\nPackaged resources")
    internal = artifact / "_internal"
    static = internal / "media_downloader" / "web" / "static"
    for name in ("index.html", "app.css", "app.js"):
        check(f"static/{name} bundled", (static / name).is_file())
    ejs = list(internal.glob("yt_dlp_ejs/**/*.js"))
    check("yt-dlp-ejs JavaScript bundled", len(ejs) >= 1, f"found {len(ejs)}")

    bundled_tools = [
        p.name
        for p in internal.rglob("*")
        if p.is_file() and p.stem.lower() in {"ffmpeg", "ffprobe", "deno"}
    ]
    check("no FFmpeg or Deno bundled", not bundled_tools, str(bundled_tools))

    # -- web UI ----------------------------------------------------------
    print("\nWeb UI")
    with tempfile.TemporaryDirectory() as tmp:
        # Each platform derives its app-data directory differently: Linux from
        # XDG_DATA_HOME, Windows from LOCALAPPDATA, macOS from HOME via
        # ~/Library/Application Support. Overriding all three keeps the run
        # isolated wherever it happens to execute.
        env = {"XDG_DATA_HOME": tmp, "LOCALAPPDATA": tmp, "HOME": tmp, "USERPROFILE": tmp}
        server = subprocess.Popen(
            [str(executable), "--web"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, **env},
        )
        # Read the address the server actually chose. Assuming a port would
        # silently test whatever else happened to be listening on it -- which
        # matters because the app falls back to an ephemeral port when its
        # preferred one is taken.
        url = ""
        deadline = time.time() + 60
        try:
            while time.time() < deadline and server.poll() is None:
                line = server.stdout.readline() if server.stdout else ""
                if "http://127.0.0.1:" in line:
                    url = "http://127.0.0.1:" + line.split("http://127.0.0.1:")[1].split()[0]
                    url = url.rstrip(".,")
                    break
            check("bound to loopback", url.startswith("http://127.0.0.1:"), url)
            for _ in range(60):
                if url and http(f"{url}/")[0] == 200:
                    break
                time.sleep(0.5)

            check("server responds on loopback", bool(url) and http(f"{url}/")[0] == 200)
            if url:
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

                status, body = http(f"{url}/api/tools", token)
                check("/api/tools returns JSON", status == 200)
                if status == 200:
                    tools = {t["tool"]: t for t in json.loads(body)["tools"]}
                    check("both tools reported", set(tools) == {"ffmpeg", "deno"})

                status, body = http(f"{url}/api/diagnostics", token)
                check("/api/diagnostics returns a report", status == 200)
                if status == 200:
                    report = json.loads(body)["report"]
                    check("report has content", "Media Downloader diagnostics" in report)
                    check("report excludes the session token", token not in report)
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover
                server.kill()

        check("server exited cleanly", server.poll() is not None)
        check("port released", not url or http(f"{url}/")[0] == 0)

        # -- diagnostics on disk -----------------------------------------
        print("\nDiagnostics")
        logs = list(Path(tmp).rglob("logs/media-downloader.log"))
        check("log file written to app data", len(logs) == 1, str(logs))
        if logs:
            text = logs[0].read_text(errors="replace")
            check("startup was logged", "startup version=" in text)
            check("server bind was logged", "server listening" in text)
            check("log contains no session token", "X-MD-Token:" not in text)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All frozen-artifact checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
