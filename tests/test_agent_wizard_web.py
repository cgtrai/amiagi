from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.agent_wizard import AgentWizardService
from amiagi.interfaces.web.routes.api_routes import api_routes


class _FakeFactory:
    def create_agent(self, descriptor, client=None):
        return SimpleNamespace(agent_id=descriptor.agent_id, descriptor=descriptor)


def _make_app(tmp_path: Path) -> Starlette:
    app = Starlette(routes=list(api_routes))
    factory = _FakeFactory()
    app.state.agent_factory = factory
    app.state.agent_wizard_service = AgentWizardService(
        factory=cast(Any, factory),
        blueprints_dir=tmp_path / "blueprints",
    )
    return app


def test_agent_wizard_start_returns_blueprint(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))

    response = client.post(
        "/api/agents/wizard/start",
        json={"need": "I need a Python code reviewer that checks tests and style."},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["session_id"]
    assert payload["step"] == 2
    assert payload["analysis"]["suggested_role"] in {"executor", "specialist", "supervisor"}
    assert payload["blueprint"]["name"]
    assert isinstance(payload["blueprint"]["required_tools"], list)


def test_agent_wizard_create_flow_returns_agent_id(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    start = client.post(
        "/api/agents/wizard/start",
        json={"need": "Create a research assistant for code and docs."},
    ).json()

    response = client.post(
        "/api/agents/wizard/step",
        json={
            "session_id": start["session_id"],
            "action": "create",
            "blueprint": start["blueprint"],
            "sponsor_confirmed": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["ok"] is True
    assert payload["agent_id"]
    assert payload["blueprint_path"].endswith(".yaml")


def test_agent_wizard_requires_confirmation_for_sensitive_permissions(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    start = client.post(
        "/api/agents/wizard/start",
        json={"need": "Create a deploy assistant."},
    ).json()
    blueprint = start["blueprint"]
    blueprint["initial_permissions"] = {
        "shell_access": True,
        "network_access": True,
        "allowed_paths": ["*"],
    }

    response = client.post(
        "/api/agents/wizard/step",
        json={
            "session_id": start["session_id"],
            "action": "create",
            "blueprint": blueprint,
            "sponsor_confirmed": False,
        },
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["requires_confirmation"] is True
    assert payload["sensitive_permissions"]


def test_agents_template_contains_wizard_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/agents.html").read_text(encoding="utf-8")

    assert "openAgentWizard" in template
    assert "/api/agents/wizard/start" in template
    assert "/api/agents/wizard/step" in template
    assert "WIZARD_STEPS" in template
