"""Tests for TeamDefinition (Phase 11)."""

from __future__ import annotations

from pathlib import Path

from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition


class TestAgentDescriptor:
    def test_defaults(self) -> None:
        d = AgentDescriptor(role="dev")
        assert d.name == ""
        assert d.model_backend == "ollama"
        assert d.skills == []

    def test_roundtrip(self) -> None:
        d = AgentDescriptor(role="dev", name="Dev", skills=["python"])
        data = d.to_dict()
        d2 = AgentDescriptor.from_dict(data)
        assert d2.role == "dev"
        assert d2.skills == ["python"]


class TestTeamDefinition:
    def test_add_remove_member(self) -> None:
        t = TeamDefinition(team_id="t1")
        t.add_member(AgentDescriptor(role="dev"))
        t.add_member(AgentDescriptor(role="qa"))
        assert t.size == 2
        assert t.remove_member("dev") is True
        assert t.size == 1
        assert t.remove_member("dev") is False

    def test_get_member(self) -> None:
        t = TeamDefinition(team_id="t1")
        t.add_member(AgentDescriptor(role="dev", name="Anna"))
        member = t.get_member("dev")
        assert member is not None
        assert member.name == "Anna"
        assert t.get_member("x") is None

    def test_roundtrip(self) -> None:
        t = TeamDefinition(
            team_id="t1",
            name="Team",
            members=[AgentDescriptor(role="a")],
            lead_agent_id="a",
            workflow="seq",
        )
        d = t.to_dict()
        t2 = TeamDefinition.from_dict(d)
        assert t2.team_id == "t1"
        assert len(t2.members) == 1
        assert t2.lead_agent_id == "a"

    def test_save_load_json(self, tmp_path: Path) -> None:
        t = TeamDefinition(team_id="t1", name="Test")
        t.add_member(AgentDescriptor(role="dev"))
        path = tmp_path / "team.json"
        t.save_json(path)
        t2 = TeamDefinition.load_json(path)
        assert t2.team_id == "t1"
        assert t2.size == 1

    def test_size_empty(self) -> None:
        t = TeamDefinition(team_id="t1")
        assert t.size == 0

    def test_metadata(self) -> None:
        t = TeamDefinition(team_id="t1", metadata={"env": "prod"})
        d = t.to_dict()
        assert d["metadata"]["env"] == "prod"

    def test_save_load_yaml(self, tmp_path: Path) -> None:
        t = TeamDefinition(team_id="yaml_team", name="YAML Team")
        t.add_member(AgentDescriptor(role="dev", name="Anna", persona_prompt="Be precise.", model_preference="gpt-4"))
        path = tmp_path / "team.yaml"
        t.save_yaml(path)
        t2 = TeamDefinition.load_yaml(path)
        assert t2.team_id == "yaml_team"
        assert t2.size == 1
        m = t2.get_member("dev")
        assert m is not None
        assert m.name == "Anna"
        assert m.persona_prompt == "Be precise."
        assert m.model_preference == "gpt-4"

    def test_agent_descriptor_new_fields_defaults(self) -> None:
        d = AgentDescriptor(role="tester")
        assert d.persona_prompt == ""
        assert d.model_preference == ""

    def test_agent_descriptor_new_fields_roundtrip(self) -> None:
        d = AgentDescriptor(
            role="dev",
            persona_prompt="You are a senior dev.",
            model_preference="llama3",
        )
        data = d.to_dict()
        assert data["persona_prompt"] == "You are a senior dev."
        assert data["model_preference"] == "llama3"
        restored = AgentDescriptor.from_dict(data)
        assert restored.persona_prompt == "You are a senior dev."
        assert restored.model_preference == "llama3"
