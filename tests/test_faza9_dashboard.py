"""Tests for Faza 9 — Dashboard integration & model management.

Covers audit criteria 9.1–9.6:
- 9.1: Monitoring tab displays same data as standalone dashboard
- 9.2: Teams tab displays data from /api/teams
- 9.3: GET /models → list models (mock Ollama)
- 9.4: POST /agents/{id}/model → model change → visible in /agents
- 9.5: PUT /models/config → writes to data/model_config.json
- 9.6: Agent config UI: persona, model, skills form
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 9.1 — Monitoring tab (data compatibility)
# ---------------------------------------------------------------------------

class TestMonitoringTab:
    """9.1: Monitoring tab displays same data as standalone."""

    def test_dashboard_html_has_monitoring_section(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/templates/dashboard.html"
        content = path.read_text(encoding="utf-8")
        assert 'id="section-monitoring"' in content
        assert "Monitoring" in content

    def test_dashboard_has_agent_overview_panel(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/templates/dashboard.html"
        content = path.read_text(encoding="utf-8")
        assert "panel-agents-overview" in content
        assert "panel-task-board" in content
        assert "panel-metrics" in content
        assert "panel-event-log" in content
        assert "panel-costs" in content

    def test_api_agents_route_exists(self):
        from amiagi.interfaces.web.routes.api_routes import api_routes
        paths = [r.path for r in api_routes]
        assert "/api/agents" in paths

    def test_api_tasks_route_exists(self):
        from amiagi.interfaces.web.routes.api_routes import api_routes
        paths = [r.path for r in api_routes]
        assert "/api/tasks" in paths

    def test_api_metrics_route_exists(self):
        from amiagi.interfaces.web.routes.api_routes import api_routes
        paths = [r.path for r in api_routes]
        assert "/api/metrics" in paths

    def test_api_budget_route_exists(self):
        from amiagi.interfaces.web.routes.api_routes import api_routes
        paths = [r.path for r in api_routes]
        assert "/api/budget" in paths


# ---------------------------------------------------------------------------
# 9.2 — Teams tab
# ---------------------------------------------------------------------------

class TestTeamsTab:
    """9.2: Teams tab displays data from /api/teams."""

    def test_team_routes_exist(self):
        from amiagi.interfaces.web.routes.team_routes import team_routes
        paths = [r.path for r in team_routes]
        assert "/api/teams" in paths
        assert "/api/teams/{team_id}/org" in paths

    def test_dashboard_html_has_teams_section(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/templates/dashboard.html"
        content = path.read_text(encoding="utf-8")
        assert 'id="section-teams"' in content
        assert "teams-grid" in content

    def test_sections_js_loads_teams(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/static/js/sections.js"
        content = path.read_text(encoding="utf-8")
        assert "loadTeams" in content
        assert "/api/teams" in content

    @pytest.mark.asyncio
    async def test_api_teams_no_dashboard(self):
        """Without team_dashboard, returns empty teams."""
        from amiagi.interfaces.web.routes.team_routes import api_teams

        request = MagicMock()
        request.app.state = MagicMock(spec=[])  # no team_dashboard attr
        resp = await api_teams(request)
        body = json.loads(bytes(resp.body))
        assert body["teams"] == []

    @pytest.mark.asyncio
    async def test_api_teams_with_dashboard(self):
        """With team_dashboard, returns summary."""
        from amiagi.interfaces.web.routes.team_routes import api_teams

        mock_dashboard = MagicMock()
        mock_dashboard.summary.return_value = {
            "teams": [{"team_id": "t1", "name": "Alpha"}],
            "count": 1,
        }
        request = MagicMock()
        request.app.state.team_dashboard = mock_dashboard
        resp = await api_teams(request)
        body = json.loads(bytes(resp.body))
        assert body["teams"][0]["team_id"] == "t1"

    @pytest.mark.asyncio
    async def test_api_team_org(self):
        from amiagi.interfaces.web.routes.team_routes import api_team_org

        mock_dashboard = MagicMock()
        mock_dashboard.org_chart.return_value = {"team_id": "t1", "members": []}
        request = MagicMock()
        request.app.state.team_dashboard = mock_dashboard
        request.path_params = {"team_id": "t1"}
        resp = await api_team_org(request)
        body = json.loads(bytes(resp.body))
        assert body["team_id"] == "t1"

    @pytest.mark.asyncio
    async def test_api_team_org_not_found(self):
        from amiagi.interfaces.web.routes.team_routes import api_team_org

        mock_dashboard = MagicMock()
        mock_dashboard.org_chart.return_value = {}
        request = MagicMock()
        request.app.state.team_dashboard = mock_dashboard
        request.path_params = {"team_id": "nonexistent"}
        resp = await api_team_org(request)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9.3 — GET /models → list models (mock Ollama)
# ---------------------------------------------------------------------------

class TestListModels:
    """9.3: GET /models returns Ollama models."""

    def test_model_routes_exist(self):
        from amiagi.interfaces.web.routes.model_routes import model_routes
        paths = [r.path for r in model_routes]
        assert "/models" in paths
        assert "/models/config" in paths
        assert "/models/ollama/status" in paths

    @pytest.mark.asyncio
    async def test_list_models_with_ollama(self):
        from amiagi.interfaces.web.routes.model_routes import list_models

        mock_client = MagicMock()
        mock_client.list_models.return_value = ["llama3.1:8b", "mistral:7b"]
        request = MagicMock()
        request.app.state.ollama_client = mock_client
        resp = await list_models(request)
        body = json.loads(bytes(resp.body))
        assert "llama3.1:8b" in body["ollama_models"]
        assert "mistral:7b" in body["ollama_models"]

    @pytest.mark.asyncio
    async def test_list_models_no_ollama(self):
        from amiagi.interfaces.web.routes.model_routes import list_models

        request = MagicMock()
        request.app.state = MagicMock(spec=[])  # no ollama_client
        resp = await list_models(request)
        body = json.loads(bytes(resp.body))
        assert body["ollama_models"] == []

    @pytest.mark.asyncio
    async def test_ollama_status_online(self):
        from amiagi.interfaces.web.routes.model_routes import ollama_status

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.list_models.return_value = ["llama3.1:8b"]
        mock_client.base_url = "http://localhost:11434"
        request = MagicMock()
        request.app.state.ollama_client = mock_client
        resp = await ollama_status(request)
        body = json.loads(bytes(resp.body))
        assert body["available"] is True
        assert "llama3.1:8b" in body["models"]

    @pytest.mark.asyncio
    async def test_ollama_status_offline(self):
        from amiagi.interfaces.web.routes.model_routes import ollama_status

        request = MagicMock()
        request.app.state = MagicMock(spec=[])  # no ollama_client
        resp = await ollama_status(request)
        body = json.loads(bytes(resp.body))
        assert body["available"] is False


# ---------------------------------------------------------------------------
# 9.4 — POST /agents/{id}/model → change model
# ---------------------------------------------------------------------------

class TestAssignAgentModel:
    """9.4: POST /agents/{id}/model changes model, visible in /agents."""

    @pytest.mark.asyncio
    async def test_assign_model_success(self):
        from amiagi.interfaces.web.routes.model_routes import assign_agent_model

        mock_agent = MagicMock()
        mock_agent.model_name = "new-model"
        mock_agent.model_backend = "ollama"

        registry = MagicMock()
        registry.get.return_value = mock_agent
        registry.update_model = MagicMock()

        request = MagicMock()
        request.path_params = {"agent_id": "agent-1"}
        request.app.state.agent_registry = registry
        request.app.state.activity_logger = None
        request.json = AsyncMock(return_value={"model_name": "new-model", "model_backend": "ollama"})
        request.state.user = None

        resp = await assign_agent_model(request)
        body = json.loads(bytes(resp.body))
        assert body["status"] == "ok"
        registry.update_model.assert_called_once_with("agent-1", "new-model", model_backend="ollama")

    @pytest.mark.asyncio
    async def test_assign_model_not_found(self):
        from amiagi.interfaces.web.routes.model_routes import assign_agent_model

        registry = MagicMock()
        registry.get.return_value = None

        request = MagicMock()
        request.path_params = {"agent_id": "nonexistent"}
        request.app.state.agent_registry = registry
        request.json = AsyncMock(return_value={"model_name": "m"})

        resp = await assign_agent_model(request)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assign_model_no_name(self):
        from amiagi.interfaces.web.routes.model_routes import assign_agent_model

        registry = MagicMock()
        registry.get.return_value = MagicMock()

        request = MagicMock()
        request.path_params = {"agent_id": "agent-1"}
        request.app.state.agent_registry = registry
        request.json = AsyncMock(return_value={"model_name": ""})

        resp = await assign_agent_model(request)
        assert resp.status_code == 400

    def test_model_route_has_post_agents_model(self):
        from amiagi.interfaces.web.routes.model_routes import model_routes
        agent_routes = [r for r in model_routes if "/agents/" in r.path and "/model" in r.path]
        assert len(agent_routes) == 1
        assert agent_routes[0].methods is not None and "POST" in agent_routes[0].methods


# ---------------------------------------------------------------------------
# 9.5 — PUT /models/config → writes to model_config.json
# ---------------------------------------------------------------------------

class TestModelConfig:
    """9.5: PUT /models/config saves to disk."""

    @pytest.mark.asyncio
    async def test_get_model_config(self):
        from amiagi.interfaces.web.routes.model_routes import get_model_config, _MODEL_CONFIG_PATH

        # Use existing file
        if _MODEL_CONFIG_PATH.exists():
            request = MagicMock()
            resp = await get_model_config(request)
            body = json.loads(bytes(resp.body))
            assert "polluks_model" in body or "kastor_model" in body

    @pytest.mark.asyncio
    async def test_update_model_config(self):
        from amiagi.interfaces.web.routes import model_routes

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"polluks_model": "old", "polluks_source": "ollama"}, f)
            tmp_path = Path(f.name)

        # Monkey-patch the config path
        original = model_routes._MODEL_CONFIG_PATH
        model_routes._MODEL_CONFIG_PATH = tmp_path

        try:
            request = MagicMock()
            request.json = AsyncMock(return_value={"polluks_model": "new-model", "kastor_model": "deep"})
            request.app.state.activity_logger = None
            request.state.user = None

            resp = await model_routes.update_model_config(request)
            body = json.loads(bytes(resp.body))
            assert body["status"] == "updated"
            assert body["config"]["polluks_model"] == "new-model"
            assert body["config"]["kastor_model"] == "deep"

            # Verify written to disk
            saved = json.loads(tmp_path.read_text())
            assert saved["polluks_model"] == "new-model"
        finally:
            model_routes._MODEL_CONFIG_PATH = original
            tmp_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_update_model_config_no_valid_keys(self):
        from amiagi.interfaces.web.routes.model_routes import update_model_config

        request = MagicMock()
        request.json = AsyncMock(return_value={"invalid_key": "value"})

        resp = await update_model_config(request)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 9.6 — Agent config UI: persona, model, skills
# ---------------------------------------------------------------------------

class TestAgentConfig:
    """9.6: Agent config routes exist and work."""

    def test_agent_config_routes_exist(self):
        from amiagi.interfaces.web.routes.agent_config_routes import agent_config_routes
        paths = [r.path for r in agent_config_routes]
        assert "/agents/{agent_id}/config" in paths
        assert "/agents/{agent_id}/preview" in paths

    @pytest.mark.asyncio
    async def test_get_agent_config(self):
        from amiagi.interfaces.web.routes.agent_config_routes import get_agent_config

        mock_agent = MagicMock()
        mock_agent.agent_id = "a1"
        mock_agent.name = "TestAgent"
        mock_agent.role.value = "executor"
        mock_agent.model_name = "llama3"
        mock_agent.model_backend = "ollama"
        mock_agent.persona_prompt = "You are helpful."
        mock_agent.skills = ["coding", "review"]
        mock_agent.tools = ["shell"]
        mock_agent.metadata = {}

        registry = MagicMock()
        registry.get.return_value = mock_agent

        request = MagicMock()
        request.path_params = {"agent_id": "a1"}
        request.app.state.agent_registry = registry

        resp = await get_agent_config(request)
        body = json.loads(bytes(resp.body))
        assert body["agent_id"] == "a1"
        assert body["persona_prompt"] == "You are helpful."
        assert "coding" in body["skills"]

    @pytest.mark.asyncio
    async def test_update_agent_config(self):
        from amiagi.interfaces.web.routes.agent_config_routes import update_agent_config

        mock_agent = MagicMock()
        mock_agent.persona_prompt = "old"
        mock_agent.skills = []
        mock_agent.model_backend = "ollama"

        registry = MagicMock()
        registry.get.return_value = mock_agent

        request = MagicMock()
        request.path_params = {"agent_id": "a1"}
        request.app.state.agent_registry = registry
        request.app.state.activity_logger = None
        request.state.user = None
        request.json = AsyncMock(return_value={"persona_prompt": "new persona", "skills": ["code"]})

        resp = await update_agent_config(request)
        body = json.loads(bytes(resp.body))
        assert body["status"] == "ok"
        assert "persona_prompt" in body["changed"]
        assert "skills" in body["changed"]

    @pytest.mark.asyncio
    async def test_update_agent_config_no_changes(self):
        from amiagi.interfaces.web.routes.agent_config_routes import update_agent_config

        registry = MagicMock()
        registry.get.return_value = MagicMock()

        request = MagicMock()
        request.path_params = {"agent_id": "a1"}
        request.app.state.agent_registry = registry
        request.json = AsyncMock(return_value={})

        resp = await update_agent_config(request)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_agent_prompt(self):
        from amiagi.interfaces.web.routes.agent_config_routes import preview_agent_prompt

        mock_agent = MagicMock()
        mock_agent.persona_prompt = "I am helpful."
        mock_agent.skills = ["coding"]
        mock_agent.tools = ["shell"]

        registry = MagicMock()
        registry.get.return_value = mock_agent

        request = MagicMock()
        request.path_params = {"agent_id": "a1"}
        request.app.state.agent_registry = registry

        resp = await preview_agent_prompt(request)
        body = json.loads(bytes(resp.body))
        assert "I am helpful" in body["prompt_preview"]
        assert "coding" in body["prompt_preview"]

    @pytest.mark.asyncio
    async def test_agent_config_not_found(self):
        from amiagi.interfaces.web.routes.agent_config_routes import get_agent_config

        registry = MagicMock()
        registry.get.return_value = None

        request = MagicMock()
        request.path_params = {"agent_id": "nonexistent"}
        request.app.state.agent_registry = registry

        resp = await get_agent_config(request)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# App wiring — routes registered
# ---------------------------------------------------------------------------

class TestFaza9AppWiring:
    def test_app_imports_team_routes(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "team_routes" in source

    def test_app_imports_model_routes(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "model_routes" in source

    def test_app_imports_agent_config_routes(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "agent_config_routes" in source

    def test_sections_js_exists(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/static/js/sections.js"
        assert path.exists()

    def test_sections_js_has_switchSection(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/static/js/sections.js"
        content = path.read_text()
        assert "switchSection" in content

    def test_sections_js_has_saveModelConfig(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/static/js/sections.js"
        content = path.read_text()
        assert "saveModelConfig" in content
