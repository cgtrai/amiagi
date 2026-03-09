from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.permission_manager import PermissionManager
from amiagi.interfaces.web.routes.system_routes import system_command_execute


class _FakeRouterEngine:
    def __init__(self) -> None:
        self.chat_service = SimpleNamespace(
            ollama_client=SimpleNamespace(model="demo", base_url="http://localhost:11434", queue_policy=None),
            memory_repository=SimpleNamespace(
                recent_messages=lambda limit=10: [],
                search_memories=lambda query=None, limit=20: [],
            ),
        )
        self.permission_manager = PermissionManager(input_fn=lambda _prompt: "n", output_fn=lambda _text: None)
        self.script_executor = SimpleNamespace()
        self.work_dir = "."
        self.autonomous_mode = False
        self.shell_policy_path = None


class _FakeAdapter:
    def __init__(self) -> None:
        self.router_engine = _FakeRouterEngine()


def _make_client() -> TestClient:
    app = Starlette(routes=[Route("/api/system/commands/execute", system_command_execute, methods=["POST"])])
    app.state.web_adapter = _FakeAdapter()
    app.state.event_hub = type("Hub", (), {"broadcast": AsyncMock()})()
    return TestClient(app, raise_server_exceptions=False)


def test_system_command_execute_runs_help() -> None:
    client = _make_client()

    response = client.post("/api/system/commands/execute", json={"command": "/help"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["command"] == "/help"
    assert payload["web_support"] == "run"
    assert payload["output"]


def test_system_command_execute_requires_slash_command() -> None:
    client = _make_client()

    response = client.post("/api/system/commands/execute", json={"command": "hello"})

    assert response.status_code == 400
    assert response.json()["error"] == "slash command required"