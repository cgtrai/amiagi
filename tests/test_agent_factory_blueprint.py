"""Tests for AgentFactory.create_from_blueprint and create_from_yaml."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_registry import AgentRegistry
from amiagi.domain.blueprint import AgentBlueprint, TestScenario
from amiagi.infrastructure.memory_repository import MemoryRepository


def _make_blueprint(**overrides: object) -> AgentBlueprint:
    return AgentBlueprint(
        name=str(overrides.get("name", "test_agent")) if "name" in overrides else "test_agent",
        role=str(overrides.get("role", "executor")) if "role" in overrides else "executor",
        team_function=str(overrides.get("team_function", "testing code")) if "team_function" in overrides else "testing code",
        persona_prompt=str(overrides.get("persona_prompt", "You are a test agent.")) if "persona_prompt" in overrides else "You are a test agent.",
        required_skills=overrides.get("required_skills", ["python"]),  # type: ignore[arg-type]
        required_tools=overrides.get("required_tools", ["read_file", "write_file"]),  # type: ignore[arg-type]
        suggested_backend=str(overrides.get("suggested_backend", "ollama")) if "suggested_backend" in overrides else "ollama",
        test_scenarios=overrides.get("test_scenarios", []),  # type: ignore[arg-type]
        initial_permissions=overrides.get("initial_permissions", {}),  # type: ignore[arg-type]
        metadata=overrides.get("metadata", {}),  # type: ignore[arg-type]
    )


def _make_factory(tmp_path: Path) -> AgentFactory:
    registry = AgentRegistry()
    repo = MemoryRepository(tmp_path / "mem.db")
    return AgentFactory(registry=registry, memory_repository=repo, work_dir=tmp_path / "work")


# ---- create_from_blueprint ----

def test_create_from_blueprint_returns_runtime(tmp_path: Path) -> None:
    factory = _make_factory(tmp_path)
    bp = _make_blueprint()
    runtime = factory.create_from_blueprint(bp)
    assert runtime is not None
    assert runtime.agent_id


def test_create_from_blueprint_registers_agent(tmp_path: Path) -> None:
    factory = _make_factory(tmp_path)
    registry = factory._registry
    bp = _make_blueprint(name="bp_agent")
    runtime = factory.create_from_blueprint(bp)
    agent = registry.get(runtime.agent_id)
    assert agent is not None
    assert agent.name == "bp_agent"


def test_create_from_blueprint_stores_metadata(tmp_path: Path) -> None:
    factory = _make_factory(tmp_path)
    bp = _make_blueprint(
        name="meta_agent",
        initial_permissions={"shell_access": True},
    )
    runtime = factory.create_from_blueprint(bp)
    agent = factory._registry.get(runtime.agent_id)
    assert agent is not None
    assert agent.metadata.get("origin") == "blueprint"
    assert agent.metadata.get("initial_permissions") == {"shell_access": True}


def test_create_from_blueprint_maps_roles(tmp_path: Path) -> None:
    from amiagi.domain.agent import AgentRole

    factory = _make_factory(tmp_path)

    for role_str, expected in [("executor", AgentRole.EXECUTOR), ("supervisor", AgentRole.SUPERVISOR), ("specialist", AgentRole.SPECIALIST)]:
        bp = _make_blueprint(name=f"agent_{role_str}", role=role_str)
        runtime = factory.create_from_blueprint(bp)
        agent = factory._registry.get(runtime.agent_id)
        assert agent is not None
        assert agent.role == expected


# ---- create_from_yaml ----

def test_create_from_yaml_json_file(tmp_path: Path) -> None:
    factory = _make_factory(tmp_path)
    bp = _make_blueprint(name="yaml_agent")
    bp_path = tmp_path / "agent.json"
    bp_path.write_text(json.dumps(bp.to_dict(), ensure_ascii=False), encoding="utf-8")

    runtime = factory.create_from_yaml(bp_path)
    assert runtime is not None
    agent = factory._registry.get(runtime.agent_id)
    assert agent is not None
    assert agent.name == "yaml_agent"


def test_create_from_yaml_yaml_file(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
    factory = _make_factory(tmp_path)
    bp = _make_blueprint(name="yaml_native")
    bp_path = tmp_path / "agent.yaml"
    bp_path.write_text(yaml.dump(bp.to_dict(), allow_unicode=True), encoding="utf-8")

    runtime = factory.create_from_yaml(bp_path)
    assert runtime is not None
    agent = factory._registry.get(runtime.agent_id)
    assert agent is not None
    assert agent.name == "yaml_native"


def test_create_from_yaml_nonexistent_raises(tmp_path: Path) -> None:
    factory = _make_factory(tmp_path)
    with pytest.raises((FileNotFoundError, Exception)):
        factory.create_from_yaml(tmp_path / "nonexistent.json")
