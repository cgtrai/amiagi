from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from amiagi.interfaces.web.routes.agent_config_routes import (
    delete_agent_permission,
    get_agent_permissions,
    update_agent_permissions,
    update_agent_skills,
)


@pytest.mark.asyncio
async def test_get_agent_permissions_returns_allowed_and_blocked() -> None:
    agent = MagicMock()
    agent.metadata = {
        "permissions": ["workspace.read"],
        "blocked_permissions": ["network.internet"],
    }
    agent.tools = ["read_file"]
    agent.skills = ["python"]
    agent.model_backend = "ollama"
    agent.model_name = "llama3"

    registry = MagicMock()
    registry.get.return_value = agent

    request = MagicMock()
    request.path_params = {"agent_id": "a1"}
    request.app.state.agent_registry = registry

    response = await get_agent_permissions(request)
    payload = json.loads(bytes(response.body))
    assert payload["allowed"] == ["workspace.read"]
    assert payload["blocked"] == ["network.internet"]


@pytest.mark.asyncio
async def test_update_agent_permissions_adds_and_moves_permission() -> None:
    agent = MagicMock()
    agent.metadata = {"permissions": ["workspace.read"], "blocked_permissions": []}

    registry = MagicMock()
    registry.get.return_value = agent

    request = MagicMock()
    request.path_params = {"agent_id": "a1"}
    request.app.state.agent_registry = registry
    request.json = AsyncMock(return_value={"permission": "network.internet", "section": "blocked"})

    response = await update_agent_permissions(request)
    payload = json.loads(bytes(response.body))
    assert response.status_code == 200
    assert payload["blocked"] == ["network.internet"]
    assert agent.metadata["blocked_permissions"] == ["network.internet"]


@pytest.mark.asyncio
async def test_delete_agent_permission_removes_from_section() -> None:
    agent = MagicMock()
    agent.metadata = {
        "permissions": ["workspace.read", "workspace.write"],
        "blocked_permissions": ["network.internet"],
    }

    registry = MagicMock()
    registry.get.return_value = agent

    request = MagicMock()
    request.path_params = {"agent_id": "a1", "permission": "workspace.write"}
    request.app.state.agent_registry = registry
    request.json = AsyncMock(return_value={"section": "allowed"})

    response = await delete_agent_permission(request)
    payload = json.loads(bytes(response.body))
    assert response.status_code == 200
    assert payload["allowed"] == ["workspace.read"]


@pytest.mark.asyncio
async def test_update_agent_skills_replaces_list() -> None:
    agent = MagicMock()
    agent.skills = ["old"]

    registry = MagicMock()
    registry.get.return_value = agent

    request = MagicMock()
    request.path_params = {"agent_id": "a1"}
    request.app.state.agent_registry = registry
    request.json = AsyncMock(return_value={"skills": ["python", "review"]})

    response = await update_agent_skills(request)
    payload = json.loads(bytes(response.body))
    assert response.status_code == 200
    assert payload["skills"] == ["python", "review"]


def test_agents_template_contains_permissions_editor_hooks() -> None:
    from pathlib import Path

    template = Path("src/amiagi/interfaces/web/templates/agents.html").read_text(encoding="utf-8")
    assert "addAgentPerm" in template
    assert "toggleAgentPerm" in template
    assert "/api/agents/${encodeURIComponent(agentId)}/permissions" in template
