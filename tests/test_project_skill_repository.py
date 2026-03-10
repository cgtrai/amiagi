from __future__ import annotations

from pathlib import Path

from amiagi.interfaces.web.skills.project_skill_repository import ProjectSkillRepository


def test_project_skill_repository_roundtrip(tmp_path: Path) -> None:
    repo = ProjectSkillRepository(tmp_path / "skills")

    created = repo.upsert_skill(
        role="polluks",
        name="web-research-local",
        display_name="Web Research Local",
        description="Project-specific web research methodology",
        content="Check sources, compare offers, keep evidence.",
        trigger_keywords=["internet", "oferty"],
        compatible_tools=["search_web", "fetch_web"],
        compatible_roles=["polluks"],
        priority=65,
    )

    listed = repo.list_skills(role="polluks")
    loaded = repo.get_skill("polluks", "web-research-local")

    assert created.name == "web-research-local"
    assert len(listed) == 1
    assert loaded is not None
    assert loaded.display_name == "Web Research Local"
    assert loaded.compatible_tools == ["search_web", "fetch_web"]
    assert loaded.path.endswith("polluks/web-research-local.md")


def test_project_skill_repository_delete(tmp_path: Path) -> None:
    repo = ProjectSkillRepository(tmp_path / "skills")
    repo.upsert_skill(role="polluks", name="tmp-skill", content="body")

    assert repo.delete_skill("polluks", "tmp-skill") is True
    assert repo.get_skill("polluks", "tmp-skill") is None