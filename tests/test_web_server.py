"""The HTTP layer, exercised over a real loopback socket.

Binding port 0 on 127.0.0.1 is offline and works identically on Linux, macOS
and Windows, so these run in CI without touching the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.jsruntime import JSRuntimeStatus
from media_downloader.service import Environment
from media_downloader.web import server as server_module
from media_downloader.web.server import STATIC_ASSETS, WebAppConfig, WebServer


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[WebServer]:
    config = WebAppConfig(
        download_dir=tmp_path / "out",
        environment=Environment(
            ffmpeg=FFmpegStatus(ffmpeg=None, ffprobe=None),
            js_runtime=JSRuntimeStatus(name=None),
        ),
        port=0,  # ephemeral: never collides with a real instance or another test
    )
    srv = WebServer(config)
    srv.start_background()
    try:
        yield srv
    finally:
        srv.shutdown()


def call(
    srv: WebServer,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(
        srv.url + path, method=method, data=body.encode() if body is not None else None
    )
    if token is not None:
        request.add_header("X-MD-Token", token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


# -- binding and lifecycle ----------------------------------------------


def test_the_server_binds_loopback_only(live_server: WebServer) -> None:
    """Never 0.0.0.0: the LAN must not be able to reach this."""
    host, port = live_server._httpd.server_address[:2]
    assert host == "127.0.0.1"
    assert port == live_server.port != 0
    assert live_server.url == f"http://127.0.0.1:{live_server.port}"


def test_shutdown_releases_the_port(tmp_path: Path) -> None:
    config = WebAppConfig(
        download_dir=tmp_path,
        environment=Environment(FFmpegStatus(None, None), JSRuntimeStatus(None)),
        port=0,
    )
    first = WebServer(config)
    first.start_background()
    port = first.port
    first.shutdown()

    rebound = WebServer(
        WebAppConfig(
            download_dir=tmp_path,
            environment=Environment(FFmpegStatus(None, None), JSRuntimeStatus(None)),
            port=port,
        )
    )
    assert rebound.port == port
    rebound.shutdown()


def test_shutdown_is_safe_to_call_twice(live_server: WebServer) -> None:
    live_server.shutdown()
    live_server.shutdown()


def test_a_busy_port_falls_back_to_an_ephemeral_one(tmp_path: Path) -> None:
    env = Environment(FFmpegStatus(None, None), JSRuntimeStatus(None))
    first = WebServer(WebAppConfig(download_dir=tmp_path, environment=env, port=0))
    second = WebServer(WebAppConfig(download_dir=tmp_path, environment=env, port=first.port))
    try:
        assert second.port != first.port
    finally:
        first.shutdown()
        second.shutdown()


# -- static serving ------------------------------------------------------


def test_the_page_is_served_with_the_token_injected(live_server: WebServer) -> None:
    status, body, headers = call(live_server, "/")
    page = body.decode()
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert server_module.TOKEN_PLACEHOLDER not in page
    assert live_server.token in page


@pytest.mark.parametrize("path", sorted(STATIC_ASSETS))
def test_allowlisted_assets_are_served(live_server: WebServer, path: str) -> None:
    status, body, headers = call(live_server, path)
    assert status == 200
    assert body
    assert headers["Content-Type"] == STATIC_ASSETS[path]


@pytest.mark.parametrize(
    "path",
    [
        "/static/../../../etc/passwd",
        "/static/..%2f..%2fetc%2fpasswd",
        "/static/secret.py",
        "/static/",
        "/../pyproject.toml",
        "/api/downloads/../config",
    ],
)
def test_anything_outside_the_allowlist_is_a_404(live_server: WebServer, path: str) -> None:
    """The URL is never turned into a filesystem path, so traversal cannot exist."""
    status, _, _ = call(live_server, path, token=live_server.token)
    assert status == 404


def test_security_headers_are_present_and_cors_is_not(live_server: WebServer) -> None:
    _, _, headers = call(live_server, "/")
    lowered = {key.lower(): value for key, value in headers.items()}
    assert lowered["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in lowered["content-security-policy"]
    assert not any(key.startswith("access-control") for key in lowered)


# -- guards over the wire -------------------------------------------------


def test_api_requests_need_the_session_token(live_server: WebServer) -> None:
    assert call(live_server, "/api/config")[0] == 403
    assert call(live_server, "/api/config", token="wrong")[0] == 403
    assert call(live_server, "/api/config", token=live_server.token)[0] == 200


def test_a_foreign_origin_is_refused(live_server: WebServer) -> None:
    status, _, _ = call(
        live_server, "/api/config", token=live_server.token, headers={"Origin": "http://evil.test"}
    )
    assert status == 403


def test_a_rebound_host_header_is_refused(live_server: WebServer) -> None:
    """The DNS-rebinding case: right socket, wrong Host."""
    status, _, _ = call(
        live_server, "/api/config", token=live_server.token, headers={"Host": "attacker.test"}
    )
    assert status == 403


def test_writes_must_be_json(live_server: WebServer) -> None:
    request = urllib.request.Request(
        live_server.url + "/api/downloads", method="POST", data=b"url=x"
    )
    request.add_header("X-MD-Token", live_server.token)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 415


# -- API over the wire ----------------------------------------------------


def test_config_round_trips_as_json(live_server: WebServer) -> None:
    status, body, headers = call(live_server, "/api/config", token=live_server.token)
    payload = json.loads(body)
    assert status == 200
    assert "application/json" in headers["Content-Type"]
    assert payload["quality_choices"]
    assert payload["ffmpeg_available"] is False


def test_a_malformed_body_is_a_clean_400(live_server: WebServer) -> None:
    status, body, _ = call(
        live_server, "/api/downloads", method="POST", token=live_server.token, body="not json"
    )
    assert status == 400
    assert json.loads(body)["error"]["code"] == "INVALID_REQUEST"


def test_a_rejected_url_answers_the_request(live_server: WebServer) -> None:
    status, body, _ = call(
        live_server,
        "/api/downloads",
        method="POST",
        token=live_server.token,
        body=json.dumps({"url": "file:///etc/passwd"}),
    )
    assert status == 400
    assert json.loads(body)["error"]["code"] == "INVALID_URL"


def test_unknown_routes_are_404(live_server: WebServer) -> None:
    assert call(live_server, "/api/nope", token=live_server.token)[0] == 404
    assert (
        call(live_server, "/api/nope", method="POST", token=live_server.token, body="{}")[0] == 404
    )


def test_an_unknown_job_is_404_over_http(live_server: WebServer) -> None:
    status, body, _ = call(live_server, "/api/downloads/missing", token=live_server.token)
    assert status == 404
    assert json.loads(body)["error"]["code"] == "NOT_FOUND"


def test_the_job_manager_is_wired_to_the_shared_service_layer(tmp_path: Path) -> None:
    """The web UI must obtain its downloader from service.create_downloader."""
    from media_downloader.downloader import Downloader

    config = WebAppConfig(
        download_dir=tmp_path,
        environment=Environment(FFmpegStatus(None, None), JSRuntimeStatus(None)),
    )
    manager = server_module.build_job_manager(config)
    built: Any = manager._downloader_factory(progress_hook=None, postprocessor_hook=None)
    assert isinstance(built, Downloader)


def test_address_reuse_matches_the_platform_semantics() -> None:
    """SO_REUSEADDR means different things on POSIX and Windows.

    On POSIX it only allows rebinding a port in TIME_WAIT, which is what we
    want after a restart. On Windows it allows binding a port that is already
    actively listening, which would let a second instance silently share the
    port instead of falling back to a free one.
    """
    import sys

    from media_downloader.web.server import _Server

    assert _Server.allow_reuse_address is (sys.platform != "win32")
