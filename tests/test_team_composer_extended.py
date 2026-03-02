"""Tests for TeamComposer — persona prompts, model preferences, YAML templates."""

from __future__ import annotations

from pathlib import Path

from amiagi.application.team_composer import (
    TeamComposer,
    _MODEL_PREFERENCES,
    _PERSONA_PROMPTS,
)


def test_build_team_has_persona_prompts() -> None:
    tc = TeamComposer()
    team = tc.build_team("We need backend api with tests")
    for member in team.members:
        assert member.persona_prompt, f"Missing persona_prompt for role={member.role}"
        assert len(member.persona_prompt) > 10


def test_build_team_has_model_preferences() -> None:
    tc = TeamComposer()
    team = tc.build_team("Build a react frontend with design")
    for member in team.members:
        assert member.model_preference in ("small", "medium", "large", ""), (
            f"Unexpected model_preference={member.model_preference!r} for role={member.role}"
        )
        assert member.model_preference  # should not be empty


def test_persona_prompts_cover_all_keyword_roles() -> None:
    from amiagi.application.team_composer import _KEYWORD_MAP
    roles = set(_KEYWORD_MAP.values())
    for role in roles:
        assert role in _PERSONA_PROMPTS, f"Missing persona prompt for role: {role}"


def test_model_preferences_cover_all_keyword_roles() -> None:
    from amiagi.application.team_composer import _KEYWORD_MAP
    roles = set(_KEYWORD_MAP.values())
    for role in roles:
        assert role in _MODEL_PREFERENCES, f"Missing model preference for role: {role}"


def test_build_team_architect_for_long_description() -> None:
    tc = TeamComposer()
    long_desc = "We need a system that handles " + " ".join(["complex"] * 60)
    team = tc.build_team(long_desc)
    roles = [m.role for m in team.members]
    assert "architect" in roles


def test_build_team_produces_non_empty() -> None:
    tc = TeamComposer()
    team = tc.build_team("deploy docker with ci/cd and data analysis")
    assert team.size >= 2
    roles = {m.role for m in team.members}
    assert "devops" in roles or "data_analyst" in roles


def test_build_team_lead_is_first_member() -> None:
    tc = TeamComposer()
    team = tc.build_team("backend api review")
    assert team.lead_agent_id == team.members[0].role


def test_load_yaml_templates(tmp_path: "Path") -> None:
    from pathlib import Path
    import yaml

    tmpl = {
        "team_id": "tmpl-test",
        "name": "Test Team",
        "members": [
            {"role": "tester", "name": "Tester", "persona_prompt": "test prompt"},
        ],
        "lead_agent_id": "tester",
    }
    p = tmp_path / "test_team.yaml"
    p.write_text(yaml.dump(tmpl), encoding="utf-8")

    tc = TeamComposer(templates_dir=str(tmp_path))
    assert "tmpl-test" in tc.list_templates()
    team = tc.get_template("tmpl-test")
    assert team is not None
    assert team.members[0].persona_prompt == "test prompt"


def test_from_template_returns_copy() -> None:
    from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition

    tc = TeamComposer()
    td = TeamDefinition(
        team_id="tmpl-copy",
        name="Copy Team",
        members=[AgentDescriptor(role="tester", name="T")],
    )
    tc.register_template(td)
    copy = tc.from_template("tmpl-copy", project_context="My project")
    assert copy is not None
    assert copy.project_context == "My project"
    assert copy is not td
