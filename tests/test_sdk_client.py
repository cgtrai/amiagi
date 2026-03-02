"""Tests for SDKClient / AmiagiClient (Phase 10)."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

import pytest

from amiagi.infrastructure.sdk_client import AmiagiClient, SDKError


class _MockAPIHandler(BaseHTTPRequestHandler):
    """Minimal mock handler for SDK tests."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/agents":
            self._respond(200, {"agents": [{"agent_id": "a"}]})
        elif self.path == "/tasks":
            self._respond(200, {"tasks": []})
        elif self.path == "/metrics":
            self._respond(200, {"uptime": 100})
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw) if raw else {}
        if self.path == "/agents":
            self._respond(201, {"agent_id": body.get("name", "new")})
        elif self.path == "/tasks":
            self._respond(201, {"task_id": "t1"})
        elif self.path == "/workflows/run":
            self._respond(200, {"status": "started"})
        else:
            self._respond(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._respond(200, {"deleted": True})

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


@pytest.fixture()
def mock_api():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), _MockAPIHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestAmiagiClient:
    def test_list_agents(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        agents = client.list_agents()
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "a"

    def test_create_agent(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        resp = client.create_agent(name="test")
        assert resp["agent_id"] == "test"

    def test_list_tasks(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        tasks = client.list_tasks()
        assert tasks == []

    def test_create_task(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        resp = client.create_task(title="t")
        assert resp["task_id"] == "t1"

    def test_run_workflow(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        resp = client.run_workflow("wf1")
        assert resp["status"] == "started"

    def test_get_metrics(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        metrics = client.get_metrics()
        assert "uptime" in metrics

    def test_ping_success(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        assert client.ping() is True

    def test_ping_failure(self) -> None:
        client = AmiagiClient("http://127.0.0.1:1")
        assert client.ping() is False

    def test_sdk_error_404(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        with pytest.raises(SDKError) as exc_info:
            client.get("/nonexistent")
        assert exc_info.value.status == 404

    def test_repr(self) -> None:
        client = AmiagiClient("http://example.com")
        assert "example.com" in repr(client)

    def test_delete(self, mock_api: str) -> None:
        client = AmiagiClient(mock_api)
        resp = client.delete("/something")
        assert resp["deleted"] is True
