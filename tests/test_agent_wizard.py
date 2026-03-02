"""Tests for AgentBlueprint and AgentWizardService."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.agent_wizard import AgentWizardService
from amiagi.domain.blueprint import AgentBlueprint, TestScenario
from amiagi.infrastructure.memory_repository import MemoryRepository


# ------------------------------------------------------------------
# Blueprint serialization
# ------------------------------------------------------------------


class TestBlueprintSerialization:
    def _sample_blueprint(self) -> AgentBlueprint:
        return AgentBlueprint(
            name="test_agent",
            role="executor",
            team_function="code reviewer",
            persona_prompt="Jestem recenzentem kodu Python.",
            required_skills=["python", "testing"],
            required_tools=["read_file", "run_python"],
            suggested_model="qwen3:14b",
            suggested_backend="ollama",
            communication_style="formal",
            test_scenarios=[
                TestScenario(
                    name="basic_test",
                    prompt="Review this code.",
                    expected_keywords=["review", "code"],
                    max_turns=1,
                ),
            ],
            metadata={"origin": "test"},
        )

    def test_to_dict(self) -> None:
        bp = self._sample_blueprint()
        d = bp.to_dict()
        assert d["name"] == "test_agent"
        assert d["role"] == "executor"
        assert d["required_tools"] == ["read_file", "run_python"]
        assert len(d["test_scenarios"]) == 1
        assert d["test_scenarios"][0]["name"] == "basic_test"

    def test_from_dict(self) -> None:
        bp = self._sample_blueprint()
        d = bp.to_dict()
        restored = AgentBlueprint.from_dict(d)
        assert restored.name == bp.name
        assert restored.role == bp.role
        assert restored.required_skills == bp.required_skills
        assert len(restored.test_scenarios) == 1
        assert restored.test_scenarios[0].prompt == "Review this code."

    def test_round_trip(self) -> None:
        bp = self._sample_blueprint()
        d = bp.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        d2 = json.loads(json_str)
        restored = AgentBlueprint.from_dict(d2)
        assert restored.to_dict() == bp.to_dict()

    def test_from_dict_minimal(self) -> None:
        bp = AgentBlueprint.from_dict({"name": "mini"})
        assert bp.name == "mini"
        assert bp.role == "executor"
        assert bp.required_skills == []
        assert bp.test_scenarios == []


# ------------------------------------------------------------------
# AgentWizardService
# ------------------------------------------------------------------


class TestAgentWizardService:
    def _make_wizard(self, tmp_path: Path) -> AgentWizardService:
        registry = AgentRegistry()
        repo = MemoryRepository(tmp_path / "wiz.db")
        factory = AgentFactory(
            registry=registry,
            memory_repository=repo,
            work_dir=tmp_path,
        )
        bp_dir = tmp_path / "blueprints"
        return AgentWizardService(
            planner_client=None,  # heuristic mode
            factory=factory,
            blueprints_dir=bp_dir,
        )

    def test_generate_blueprint_heuristic(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        bp = wizard.generate_blueprint("I need a code reviewer for Python")
        assert bp.name  # non-empty
        assert bp.role == "executor"
        assert bp.team_function  # non-empty
        assert len(bp.required_tools) > 0

    def test_generate_blueprint_code_adds_tools(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        bp = wizard.generate_blueprint("Build a python code assistant")
        tools_lower = [t.lower() for t in bp.required_tools]
        assert any("python" in t or "write" in t for t in tools_lower)

    def test_save_and_load_blueprint(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        bp = wizard.generate_blueprint("generic assistant")
        saved = wizard.save_blueprint(bp)
        assert saved.exists()
        loaded = wizard.load_blueprint(bp.name)
        assert loaded is not None
        assert loaded.name == bp.name

    def test_list_blueprints(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        assert wizard.list_blueprints() == []
        bp = wizard.generate_blueprint("helper agent")
        wizard.save_blueprint(bp)
        names = wizard.list_blueprints()
        assert len(names) == 1
        assert names[0] == bp.name

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        assert wizard.load_blueprint("does_not_exist") is None

    def test_create_agent_from_blueprint(self, tmp_path: Path) -> None:
        wizard = self._make_wizard(tmp_path)
        bp = wizard.generate_blueprint("simple agent")
        runtime = wizard.create_agent(bp)
        assert runtime.agent_id  # non-empty
        assert runtime.descriptor.name == bp.name
