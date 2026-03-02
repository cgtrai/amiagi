"""Tests for SkillCatalog (Phase 11)."""

from __future__ import annotations

import json
from pathlib import Path

from amiagi.application.skill_catalog import SkillCatalog, SkillEntry


class TestSkillEntry:
    def test_defaults(self) -> None:
        s = SkillEntry(name="code")
        assert s.difficulty_level == "medium"
        assert s.required_tools == []

    def test_roundtrip(self) -> None:
        s = SkillEntry(name="code", tags=["python"])
        d = s.to_dict()
        s2 = SkillEntry.from_dict(d)
        assert s2.name == "code"
        assert s2.tags == ["python"]


class TestSkillCatalog:
    def test_register_and_get(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="code", description="Write code"))
        assert cat.get("code") is not None
        assert cat.count == 1

    def test_unregister(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="code"))
        assert cat.unregister("code") is True
        assert cat.unregister("code") is False
        assert cat.count == 0

    def test_list_skills(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="a"))
        cat.register(SkillEntry(name="b"))
        assert len(cat.list_skills()) == 2

    def test_search(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="code_gen", tags=["python"]))
        cat.register(SkillEntry(name="review"))
        assert len(cat.search("python")) == 1
        assert len(cat.search("code")) == 1

    def test_match_for_tools(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="web", required_tools=["browser", "curl"]))
        cat.register(SkillEntry(name="local", required_tools=["shell"]))
        matches = cat.match_for_tools(["shell", "browser", "curl"])
        assert len(matches) == 2
        matches2 = cat.match_for_tools(["shell"])
        assert len(matches2) == 1

    def test_match_for_model(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="any_model"))
        cat.register(SkillEntry(name="specific", compatible_models=["gpt-4"]))
        assert len(cat.match_for_model("gpt-4")) == 2
        assert len(cat.match_for_model("llama")) == 1

    def test_save_load_json(self, tmp_path: Path) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="a", description="desc"))
        path = tmp_path / "skills.json"
        cat.save_json(path)
        cat2 = SkillCatalog()
        count = cat2.load_json(path)
        assert count == 1
        entry = cat2.get("a")
        assert entry is not None
        assert entry.description == "desc"

    def test_to_dict(self) -> None:
        cat = SkillCatalog()
        cat.register(SkillEntry(name="x"))
        d = cat.to_dict()
        assert d["count"] == 1
        assert len(d["skills"]) == 1
