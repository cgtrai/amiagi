"""Tests for Faza 5 — Multi-agent workspace.

Covers:
- API routes (/api/agents, /api/tasks, /api/metrics, /api/budget)
- Dashboard route (/dashboard)
- EventHub per-agent listeners
- Web Component file presence
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Lightweight stubs so tests don't require full amiagi stack
# ---------------------------------------------------------------------------

class _AgentState(str, Enum):
    IDLE = "idle"
    WORKING = "working"

class _AgentRole(str, Enum):
    EXECUTOR = "executor"
    SUPERVISOR = "supervisor"

@dataclass
class _AgentDescriptor:
    agent_id: str
    name: str
    role: _AgentRole = _AgentRole.EXECUTOR
    state: _AgentState = _AgentState.IDLE
    model_backend: str = "ollama"
    model_name: str = "llama3"
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    persona_prompt: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

class _FakeRegistry:
    def __init__(self, agents: list[_AgentDescriptor] | None = None):
        self._agents = {a.agent_id: a for a in (agents or [])}

    def list_all(self):
        return list(self._agents.values())

    def get(self, agent_id):
        return self._agents.get(agent_id)

class _FakeBudgetManager:
    def summary(self):
        return {"agent-1": {"limit_usd": 1.0, "spent_usd": 0.5}}

    def task_summary(self):
        return {"task-1": {"limit_usd": 2.0, "spent_usd": 0.1}}

    def session_summary(self):
        return {"limit_usd": 10.0, "spent_usd": 0.6, "tokens_used": 420, "requests_count": 3}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agents():
    return [
        _AgentDescriptor(agent_id="a1", name="Alpha", role=_AgentRole.EXECUTOR, state=_AgentState.IDLE),
        _AgentDescriptor(agent_id="a2", name="Beta", role=_AgentRole.SUPERVISOR, state=_AgentState.WORKING),
    ]


@pytest.fixture
def app_with_agents(agents):
    """Build a minimal Starlette test app with agent_registry on state."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from amiagi.interfaces.web.routes.api_routes import api_routes

    app = Starlette(routes=[*api_routes])
    app.state.agent_registry = _FakeRegistry(agents)
    app.state.web_adapter = None
    app.state.task_queue = None
    app.state.metrics_collector = None
    app.state.budget_manager = _FakeBudgetManager()
    return TestClient(app)


@pytest.fixture
def app_empty():
    """App without any services on state."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from amiagi.interfaces.web.routes.api_routes import api_routes

    app = Starlette(routes=[*api_routes])
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/agents
# ---------------------------------------------------------------------------

class TestListAgents:
    def test_returns_agents(self, app_with_agents):
        resp = app_with_agents.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        ids = {a["agent_id"] for a in data["agents"]}
        assert ids == {"a1", "a2"}

    def test_agent_fields(self, app_with_agents):
        resp = app_with_agents.get("/api/agents")
        agent = resp.json()["agents"][0]
        for key in ("agent_id", "name", "role", "state", "model_backend", "model_name"):
            assert key in agent

    def test_empty_registry(self, app_empty):
        resp = app_empty.get("/api/agents")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestGetAgent:
    def test_existing_agent(self, app_with_agents):
        resp = app_with_agents.get("/api/agents/a1")
        assert resp.status_code == 200
        assert resp.json()["agent"]["name"] == "Alpha"

    def test_missing_agent(self, app_with_agents):
        resp = app_with_agents.get("/api/agents/nonexistent")
        assert resp.status_code == 404

    def test_no_registry(self, app_empty):
        resp = app_empty.get("/api/agents/a1")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /api/tasks
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_no_task_queue(self, app_empty):
        resp = app_empty.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_empty_task_queue(self, app_with_agents):
        resp = app_with_agents.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# /api/metrics
# ---------------------------------------------------------------------------

class TestGetMetrics:
    def test_no_collector(self, app_empty):
        resp = app_empty.get("/api/metrics")
        assert resp.status_code == 200
        assert resp.json()["metrics"] == {}

    def test_with_collector_summary(self, app_with_agents):
        app_with_agents.app.state.metrics_collector = MagicMock(summary=lambda: {"avg_latency": 0.42})
        resp = app_with_agents.get("/api/metrics")
        assert resp.json()["metrics"]["avg_latency"] == 0.42


# ---------------------------------------------------------------------------
# /api/budget
# ---------------------------------------------------------------------------

class TestGetBudget:
    def test_returns_summaries(self, app_with_agents):
        resp = app_with_agents.get("/api/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "session" in data
        assert data["session"]["tokens_used"] == 420

    def test_no_budget_manager(self, app_empty):
        resp = app_empty.get("/api/budget")
        assert resp.status_code == 200
        assert resp.json()["session"] == {}


# ---------------------------------------------------------------------------
# EventHub per-agent listeners
# ---------------------------------------------------------------------------

class TestEventHubAgentListeners:
    def test_register_unregister(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        fake_ws = MagicMock()
        hub.register_agent_listener("a1", fake_ws)
        assert hub.agent_listener_count == 1
        hub.unregister_agent_listener("a1", fake_ws)
        assert hub.agent_listener_count == 0

    def test_unregister_wrong_agent(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        fake_ws = MagicMock()
        hub.register_agent_listener("a1", fake_ws)
        hub.unregister_agent_listener("a2", fake_ws)  # different agent
        assert hub.agent_listener_count == 1  # still registered

    @pytest.mark.asyncio
    async def test_broadcast_to_agent_listener(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = AsyncMock()
        hub.register_agent_listener("a1", ws)
        await hub.broadcast("log", {"agent_id": "a1", "message": "hello"})
        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "log"
        assert payload["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_broadcast_does_not_leak_to_wrong_agent(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws_a1 = AsyncMock()
        ws_a2 = AsyncMock()
        hub.register_agent_listener("a1", ws_a1)
        hub.register_agent_listener("a2", ws_a2)
        await hub.broadcast("log", {"agent_id": "a1", "message": "for a1 only"})
        ws_a1.send_text.assert_called_once()
        ws_a2.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_global_plus_agent(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        global_ws = AsyncMock()
        agent_ws = AsyncMock()
        hub._connections.append(global_ws)
        hub.register_agent_listener("a1", agent_ws)
        await hub.broadcast("log", {"agent_id": "a1", "message": "test"})
        # Both should receive
        assert global_ws.send_text.call_count == 1
        assert agent_ws.send_text.call_count == 1


# ---------------------------------------------------------------------------
# Dashboard route
# ---------------------------------------------------------------------------

class TestDashboardRoute:
    def test_root_redirects_to_dashboard(self):
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes

        app = Starlette(routes=[*dashboard_routes])
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Web Component files exist
# ---------------------------------------------------------------------------

class TestWebComponentFiles:
    COMPONENTS = [
        "agent-card.js",
        "chat-stream.js",
        "task-board.js",
        "event-ticker.js",
        "metric-card.js",
    ]

    @pytest.mark.parametrize("filename", COMPONENTS)
    def test_component_file_exists(self, filename):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "components" / filename
        assert path.exists(), f"Web Component file missing: {path}"

    @pytest.mark.parametrize("filename", COMPONENTS)
    def test_component_defines_custom_element(self, filename):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "components" / filename
        content = path.read_text()
        assert "customElements.define" in content, f"{filename} missing customElements.define"


# ---------------------------------------------------------------------------
# Dashboard JS file exists
# ---------------------------------------------------------------------------

class TestDashboardJS:
    def test_dashboard_js_exists(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "dashboard.js"
        assert path.exists()

    def test_dashboard_js_has_ws_connect(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "dashboard.js"
        content = path.read_text()
        assert "WebSocket" in content
        assert "connectGlobalWS" in content


# ---------------------------------------------------------------------------
# API route: /api/agents/state
# ---------------------------------------------------------------------------

class TestGetAgentState:
    def test_with_adapter(self, app_with_agents):
        engine = MagicMock()
        engine.actor_states = {"router": "idle", "supervisor": "idle"}
        engine.router_cycle_in_progress = False
        adapter = MagicMock()
        adapter.router_engine = engine
        app_with_agents.app.state.web_adapter = adapter

        resp = app_with_agents.get("/api/agents/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "actor_states" in data
        assert data["actor_states"]["router"] == "idle"

    def test_without_adapter(self, app_empty):
        resp = app_empty.get("/api/agents/state")
        assert resp.status_code == 200
        assert resp.json()["actor_states"] == {}
