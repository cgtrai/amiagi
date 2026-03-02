"""Tests for RESTServer (Phase 10)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error
from typing import Any

import pytest

from amiagi.infrastructure.rest_server import RESTServer


def _get(url: str, token: str = "") -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode()) if exc.fp else {}
        return exc.code, body


def _post(url: str, body: dict[str, Any], token: str = "") -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_resp = json.loads(exc.read().decode()) if exc.fp else {}
        return exc.code, body_resp


@pytest.fixture()
def server():
    srv = RESTServer(port=0)  # port=0 won't work for HTTPServer; use a random high port
    # Use a high random port to avoid conflicts
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = RESTServer(port=port)
    srv.add_route("GET", "/health", lambda body: (200, {"status": "ok"}))
    srv.add_route("POST", "/echo", lambda body: (200, {"echo": body}))
    srv.start()
    time.sleep(0.1)
    yield srv
    srv.stop()


@pytest.fixture()
def auth_server():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = RESTServer(port=port, bearer_token="secret123")
    srv.add_route("GET", "/secure", lambda body: (200, {"ok": True}))
    srv.start()
    time.sleep(0.1)
    yield srv
    srv.stop()


class TestRESTServer:
    def test_get_health(self, server: RESTServer) -> None:
        status, body = _get(f"{server.address}/health")
        assert status == 200
        assert body["status"] == "ok"

    def test_post_echo(self, server: RESTServer) -> None:
        status, body = _post(f"{server.address}/echo", {"msg": "hi"})
        assert status == 200
        assert body["echo"]["msg"] == "hi"

    def test_not_found(self, server: RESTServer) -> None:
        status, body = _get(f"{server.address}/nonexistent")
        assert status == 404

    def test_auth_required(self, auth_server: RESTServer) -> None:
        status, _ = _get(f"{auth_server.address}/secure")
        assert status == 401

    def test_auth_valid(self, auth_server: RESTServer) -> None:
        status, body = _get(f"{auth_server.address}/secure", token="secret123")
        assert status == 200
        assert body["ok"] is True

    def test_auth_invalid_token(self, auth_server: RESTServer) -> None:
        status, _ = _get(f"{auth_server.address}/secure", token="wrong")
        assert status == 401

    def test_is_running(self, server: RESTServer) -> None:
        assert server.is_running is True
        server.stop()
        assert server.is_running is False

    def test_list_routes(self, server: RESTServer) -> None:
        routes = server.list_routes()
        assert len(routes) == 2
        assert {"method": "GET", "path": "/health"} in routes

    def test_to_dict(self, server: RESTServer) -> None:
        d = server.to_dict()
        assert d["is_running"] is True
        assert isinstance(d["routes"], list)

    def test_double_start(self, server: RESTServer) -> None:
        server.start()  # should be a no-op
        assert server.is_running is True

    def test_parametric_route(self, server: RESTServer) -> None:
        server.add_route(
            "GET",
            "/items/{id}",
            lambda body: (200, {"id": body.get("_path_params", {}).get("id", ""), "path": body.get("_path", "")}),
        )
        status, body = _get(f"{server.address}/items/abc123")
        assert status == 200
        assert body["id"] == "abc123"
        assert body["path"] == "/items/abc123"

    def test_extract_path_params(self) -> None:
        srv = RESTServer()
        params = srv.extract_path_params("/tasks/{id}", "/tasks/xyz")
        assert params == {"id": "xyz"}

    def test_extract_path_params_multiple(self) -> None:
        srv = RESTServer()
        params = srv.extract_path_params("/orgs/{org}/repos/{repo}", "/orgs/acme/repos/main")
        assert params == {"org": "acme", "repo": "main"}

    def test_push_event_and_get_events(self, server: RESTServer) -> None:
        server.wire_domain_routes()
        server.push_event({"type": "test", "data": "hello"})
        server.push_event({"type": "test", "data": "world"})
        status, body = _get(f"{server.address}/events")
        assert status == 200
        events = body.get("events", [])
        assert len(events) == 2
        assert events[0]["data"] == "hello"

    def test_wire_domain_routes_returns_count(self) -> None:
        srv = RESTServer()
        # With no domain objects, only the /events route is added
        count = srv.wire_domain_routes()
        assert count >= 1  # at least /events
