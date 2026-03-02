"""Tests for TeamComposer (Phase 11)."""

from __future__ import annotations

from pathlib import Path

from amiagi.application.team_composer import CompositionAdvice, TeamComposer
from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition


class TestCompositionAdvice:
    def test_to_dict(self) -> None:
        a = CompositionAdvice(recommended_roles=["dev"], team_size=1)
        d = a.to_dict()
        assert d["team_size"] == 1


class TestTeamComposer:
    def test_recommend_backend(self) -> None:
        c = TeamComposer()
        advice = c.recommend("Build a REST API backend with database")
        assert "backend_developer" in advice.recommended_roles

    def test_recommend_frontend(self) -> None:
        c = TeamComposer()
        advice = c.recommend("Create a React frontend UI dashboard")
        assert "frontend_developer" in advice.recommended_roles

    def test_recommend_fallback(self) -> None:
        c = TeamComposer()
        advice = c.recommend("xyz")
        # Falls back to generic team
        assert len(advice.recommended_roles) >= 1

    def test_complex_adds_architect(self) -> None:
        c = TeamComposer()
        desc = "Build a " + " ".join(["complex system"] * 30) + " with api and test"
        advice = c.recommend(desc)
        assert "architect" in advice.recommended_roles

    def test_build_team(self) -> None:
        c = TeamComposer()
        team = c.build_team("Build a backend with tests", team_id="t1")
        assert team.team_id == "t1"
        assert team.size >= 1
        assert team.lead_agent_id != ""

    def test_build_team_auto_id(self) -> None:
        c = TeamComposer()
        team = c.build_team("Something")
        assert team.team_id.startswith("team-")

    def test_register_template(self, tmp_path: Path) -> None:
        c = TeamComposer()
        tmpl = TeamDefinition(team_id="custom", name="Custom")
        tmpl.add_member(AgentDescriptor(role="dev"))
        c.register_template(tmpl)
        assert "custom" in c.list_templates()

    def test_from_template(self) -> None:
        c = TeamComposer()
        tmpl = TeamDefinition(team_id="tmpl", name="T")
        tmpl.add_member(AgentDescriptor(role="dev"))
        c.register_template(tmpl)
        team = c.from_template("tmpl", project_context="ctx")
        assert team is not None
        assert team.project_context == "ctx"

    def test_from_template_not_found(self) -> None:
        c = TeamComposer()
        assert c.from_template("nope") is None

    def test_history(self) -> None:
        c = TeamComposer()
        c.recommend("api backend with tests")
        assert len(c.history()) == 1

    def test_templates_dir(self, tmp_path: Path) -> None:
        tmpl = TeamDefinition(team_id="loaded", name="Loaded")
        tmpl.save_json(tmp_path / "loaded.json")
        c = TeamComposer(templates_dir=str(tmp_path))
        assert "loaded" in c.list_templates()
