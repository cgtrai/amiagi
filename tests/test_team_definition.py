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
