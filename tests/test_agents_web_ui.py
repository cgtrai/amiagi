from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.domain.agent import AgentState
from amiagi.interfaces.web.routes.inbox_routes import agent_restart, inbox_routes


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


def test_agent_lifecycle_routes_include_restart() -> None:
    paths = {route.path: (route.methods or set()) for route in inbox_routes}

    assert "/api/agents/{agent_id}/restart" in paths
    assert "POST" in paths["/api/agents/{agent_id}/restart"]


def test_restart_route_falls_back_to_registry_idle_state() -> None:
    app = Starlette(routes=[Route("/api/agents/{agent_id}/restart", agent_restart, methods=["POST"])])
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(state=AgentState.IDLE)
    app.state.agent_registry = registry
    app.state.event_hub = SimpleNamespace(broadcast=AsyncMock())
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/agents/a-1/restart")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    registry.update_state.assert_called_once_with("a-1", AgentState.IDLE, reason="operator:restart")


def test_agents_template_covers_tokens_cost_restart_and_drawer_editors() -> None:
    html = (_WEB_ROOT / "templates" / "agents.html").read_text(encoding="utf-8")

    assert "token_count || 0" in html
    assert "cost_usd || 0" in html
    assert "\\'restart\\'" in html
    assert ">Run</button>" in html
    assert ">Restart</button>" in html
    assert ">Delete</button>" in html
    assert "▶ Run" not in html
    assert "↻ Restart" not in html
    assert "🗑 Delete" not in html
    assert "/models" in html
    assert "class=\"skill-check\"" in html
    assert "glass-pill\">Allowed" in html
    assert "glass-pill\">Blocked" in html
    assert "openAgentWizard" in html
    assert "/api/agents/wizard/start" in html
    assert "/api/agents/wizard/step" in html
    assert "renderBackendOptions" in html
    assert "renderAgentsState" in html
    assert "agents-state-card" in html
