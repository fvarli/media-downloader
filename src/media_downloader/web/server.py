"""The loopback HTTP server.

A ``ThreadingHTTPServer`` with a small hand-written router. Static assets are
served from a fixed allowlist rather than by mapping the URL onto a path, so
there is no traversal surface at all: an unknown name is simply a 404.

Downloads run on their own worker thread (see :mod:`.jobs`), so a request never
occupies a handler for the length of a download.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from media_downloader.logging_setup import get_logger
from media_downloader.service import Environment, create_downloader
from media_downloader.web import api
from media_downloader.web.jobs import JobManager
from media_downloader.web.security import (
    SECURITY_HEADERS,
    TOKEN_PLACEHOLDER,
    RequestGuard,
    generate_token,
)

logger = get_logger("web.server")


class AssetMissingError(RuntimeError):
    """A packaged static asset could not be read (a packaging fault)."""


LOOPBACK_HOST = "127.0.0.1"
PREFERRED_PORT = 8765
MAX_BODY_BYTES = 64 * 1024

#: The only files this server will ever serve, and their content types.
#: Serving from an allowlist rather than a directory removes path traversal as
#: a category of bug rather than defending against it.
STATIC_ASSETS: dict[str, str] = {
    "/static/app.css": "text/css; charset=utf-8",
    "/static/app.js": "text/javascript; charset=utf-8",
}


def read_asset(name: str) -> bytes:
    """Read a packaged asset.

    ``importlib.resources`` rather than ``__file__`` arithmetic, so this keeps
    working inside a PyInstaller bundle where the package is not a real
    directory on disk.
    """
    try:
        return (resources.files("media_downloader.web") / "static" / name).read_bytes()
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - packaging fault
        raise AssetMissingError(f"Packaged asset is missing: {name}") from exc


@dataclass
class WebAppConfig:
    """Everything a server instance needs."""

    download_dir: Path
    environment: Environment
    host: str = LOOPBACK_HOST
    port: int = PREFERRED_PORT
    verbose: bool = False


def build_job_manager(config: WebAppConfig) -> JobManager:
    """Wire the job manager to the shared service layer.

    The web UI gets its downloader from exactly the same factory the CLI uses,
    which is what keeps a single download implementation.
    """

    def factory(**hooks: Any) -> Any:
        return create_downloader(config.environment, verbose=config.verbose, **hooks)

    return JobManager(factory)


class _Handler(BaseHTTPRequestHandler):
    """Routes requests. One instance per request, per stdlib design."""

    server_version = "MediaDownloader"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, context: api.ApiContext, guard: RequestGuard, **kw: Any) -> None:
        self._ctx = context
        self._guard = guard
        super().__init__(*args, **kw)

    # -- plumbing --------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Route access logs to our logger instead of stderr."""
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send(self, status: int, body: bytes = b"", content_type: str | None = None) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for header, value in SECURITY_HEADERS.items():
            self.send_header(header, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any] | None) -> None:
        if payload is None or status == HTTPStatus.NO_CONTENT:
            self._send(status)
            return
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _deny(self, status: int, message: str) -> None:
        self._send_json(status, {"error": {"code": "FORBIDDEN", "message": message, "hint": None}})

    def _drain_body(self) -> bytes:
        """Consume the request body.

        This must happen even when the request is about to be rejected: on a
        keep-alive connection, bytes left unread would be parsed as the start
        of the next request and hang the client.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY_BYTES))

    @staticmethod
    def _parse_json(raw: bytes) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _guard_request(self, *, api_call: bool, write: bool) -> bool:
        """Apply the layered localhost defences. False means already answered."""
        if not self._guard.check_host(self.headers.get("Host")):
            self._deny(HTTPStatus.FORBIDDEN, "Invalid Host header.")
            return False
        if not self._guard.check_origin(self.headers.get("Origin")):
            self._deny(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
            return False
        if api_call and not self._guard.check_token(self.headers.get("X-MD-Token")):
            self._deny(HTTPStatus.FORBIDDEN, "Missing or invalid session token.")
            return False
        if write and not self._guard.check_content_type(self.headers.get("Content-Type")):
            self._deny(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected application/json.")
            return False
        return True

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # stdlib handler naming
        path = self.path.split("?", 1)[0]
        is_api = path.startswith("/api/")
        if not self._guard_request(api_call=is_api, write=False):
            return

        try:
            self._route_get(path)
        except AssetMissingError as exc:
            logger.error("%s", exc)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": {"code": "ASSET_MISSING", "message": str(exc), "hint": None}},
            )

    def _route_get(self, path: str) -> None:
        if path in ("/", "/index.html"):
            page = read_asset("index.html").replace(
                TOKEN_PLACEHOLDER.encode(), self._guard.token.encode()
            )
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            return

        if path in STATIC_ASSETS:
            self._send(HTTPStatus.OK, read_asset(Path(path).name), STATIC_ASSETS[path])
            return

        if path == "/api/config":
            self._send_json(*api.get_config(self._ctx))
            return
        if path == "/api/downloads":
            self._send_json(*api.list_downloads(self._ctx))
            return
        if path.startswith("/api/downloads/"):
            self._send_json(*api.get_download(self._ctx, path.removeprefix("/api/downloads/")))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": path}})

    def do_POST(self) -> None:  # stdlib handler naming
        path = self.path.split("?", 1)[0]
        # Drain first: a rejected request still has to leave the connection in
        # a usable state.
        raw = self._drain_body()
        if not self._guard_request(api_call=True, write=True):
            return

        if path == "/api/downloads":
            body = self._parse_json(raw)
            if body is None:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "INVALID_REQUEST", "message": "Expected a JSON object."}},
                )
                return
            self._send_json(*api.create_download(self._ctx, body))
            return

        if path == "/api/open-folder":
            self._send_json(*api.open_download_folder(self._ctx))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": {"code": "NOT_FOUND", "message": path}})


class WebServer:
    """Owns the socket, the job manager and the shutdown sequence."""

    def __init__(self, config: WebAppConfig) -> None:
        self.config = config
        self.jobs = build_job_manager(config)
        self.token = generate_token()
        # ThreadingHTTPServer.shutdown() blocks until serve_forever() returns,
        # so calling it on a server that never started would deadlock. Track
        # whether we are serving to keep shutdown() safe in any order.
        self._serving = False
        self._closed = False
        self._httpd = self._bind()
        self.port = int(self._httpd.server_address[1])
        self.guard = RequestGuard(token=self.token, port=self.port)
        self._httpd.RequestHandlerClass = partial(
            _Handler,
            context=api.ApiContext(
                jobs=self.jobs,
                environment=config.environment,
                download_dir=config.download_dir,
            ),
            guard=self.guard,
        )

    def _bind(self) -> ThreadingHTTPServer:
        """Bind loopback, preferring a stable port but never failing over one."""
        for port in (self.config.port, 0):
            try:
                return ThreadingHTTPServer((self.config.host, port), BaseHTTPRequestHandler)
            except OSError:
                logger.debug("Port %s unavailable; trying an ephemeral port.", port)
        raise OSError("No loopback port could be bound.")  # pragma: no cover

    @property
    def url(self) -> str:
        return f"http://{self.config.host}:{self.port}"

    def serve_forever(self) -> None:
        self._serving = True
        try:
            self._httpd.serve_forever(poll_interval=0.2)
        finally:
            self._serving = False

    def start_background(self) -> threading.Thread:
        """Run the server on a thread. Used by tests and by the launcher."""
        thread = threading.Thread(target=self.serve_forever, daemon=True, name="md-web")
        thread.start()
        # Wait briefly for the loop to take hold so a shutdown() immediately
        # afterwards is not racing the thread start.
        deadline = time.monotonic() + 2.0
        while not self._serving and time.monotonic() < deadline:
            time.sleep(0.005)
        return thread

    def shutdown(self) -> None:
        """Stop serving and release the socket.

        Safe to call more than once, and safe on a server that was created but
        never served -- which is exactly what a failed startup looks like.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self._serving:
                self._httpd.shutdown()
        finally:
            self._httpd.server_close()

    def __enter__(self) -> WebServer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()


def find_free_port(host: str = LOOPBACK_HOST) -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
