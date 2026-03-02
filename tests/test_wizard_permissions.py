"""Tests for SensitivePermissionError and Sponsor confirmation in AgentWizardService."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.agent_wizard import (
    AgentWizardService,
    SensitivePermissionError,
)
from amiagi.domain.blueprint import AgentBlueprint
from amiagi.infrastructure.memory_repository import MemoryRepository


def _make_blueprint(**overrides: object) -> AgentBlueprint:
    return AgentBlueprint(
        name=str(overrides.get("name", "test_agent")) if "name" in overrides else "test_agent",
        role=str(overrides.get("role", "executor")) if "role" in overrides else "executor",
        team_function=str(overrides.get("team_function", "testing")) if "team_function" in overrides else "testing",
        persona_prompt=str(overrides.get("persona_prompt", "test persona")) if "persona_prompt" in overrides else "test persona",
        required_skills=overrides.get("required_skills", []),  # type: ignore[arg-type]
        required_tools=overrides.get("required_tools", ["read_file"]),  # type: ignore[arg-type]
        test_scenarios=overrides.get("test_scenarios", []),  # type: ignore[arg-type]
        initial_permissions=overrides.get("initial_permissions", {}),  # type: ignore[arg-type]
    )


def _make_wizard(tmp_path: Path) -> AgentWizardService:
    registry = AgentRegistry()
    repo = MemoryRepository(tmp_path / "mem.db")
    factory = AgentFactory(registry=registry, memory_repository=repo, work_dir=tmp_path / "work")
    return AgentWizardService(factory=factory, blueprints_dir=tmp_path / "blueprints")


# ---- SensitivePermissionError ----

def test_sensitive_permission_error_attributes() -> None:
    bp = _make_blueprint(initial_permissions={"shell_access": True})
    err = SensitivePermissionError(bp, ["shell_access (uruchamianie poleceń systemowych)"])
    assert err.blueprint is bp
    assert len(err.sensitive_perms) == 1
    assert "shell_access" in str(err)


# ---- check_sensitive_permissions ----

def test_check_no_sensitive_perms() -> None:
    bp = _make_blueprint(initial_permissions={})
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert result == []


def test_check_no_permissions_field() -> None:
    bp = _make_blueprint()
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert result == []


def test_check_shell_access_sensitive() -> None:
    bp = _make_blueprint(initial_permissions={"shell_access": True})
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert len(result) == 1
    assert "shell_access" in result[0]


def test_check_network_access_sensitive() -> None:
    bp = _make_blueprint(initial_permissions={"network_access": True})
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert len(result) == 1
    assert "network_access" in result[0]


def test_check_unrestricted_paths_sensitive() -> None:
    bp = _make_blueprint(initial_permissions={"allowed_paths": ["*"]})
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert len(result) == 1
    assert "unrestricted" in result[0].lower() or "path" in result[0].lower()


def test_check_multiple_sensitive() -> None:
    bp = _make_blueprint(initial_permissions={
        "shell_access": True,
        "network_access": True,
        "allowed_paths": ["/"],
    })
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert len(result) == 3


def test_check_false_values_not_sensitive() -> None:
    bp = _make_blueprint(initial_permissions={
        "shell_access": False,
        "network_access": False,
    })
    result = AgentWizardService.check_sensitive_permissions(bp)
    assert result == []


# ---- create_agent with sponsor_confirmed ----

def test_create_agent_no_sensitive_perms_succeeds(tmp_path: Path) -> None:
    wizard = _make_wizard(tmp_path)
    bp = _make_blueprint(initial_permissions={})
    runtime = wizard.create_agent(bp)
    assert runtime is not None


def test_create_agent_sensitive_perms_raises_without_confirm(tmp_path: Path) -> None:
    wizard = _make_wizard(tmp_path)
    bp = _make_blueprint(initial_permissions={"shell_access": True})
    with pytest.raises(SensitivePermissionError) as exc_info:
        wizard.create_agent(bp)
    assert exc_info.value.blueprint is bp
    assert len(exc_info.value.sensitive_perms) == 1


def test_create_agent_sensitive_perms_succeeds_with_confirm(tmp_path: Path) -> None:
    wizard = _make_wizard(tmp_path)
    bp = _make_blueprint(initial_permissions={"shell_access": True, "network_access": True})
    runtime = wizard.create_agent(bp, sponsor_confirmed=True)
    assert runtime is not None
