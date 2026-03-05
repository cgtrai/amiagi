"""Tests for SkillSelector — multi-level matching pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from amiagi.interfaces.web.skills.skill_selector import SelectedSkill, SkillSelector


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _skill_row(
    name: str = "python",
    keywords: list[str] | None = None,
    tools: list[str] | None = None,
    roles: list[str] | None = None,
    token_cost: int = 100,
    priority: int = 50,
    pinned: bool = False,
) -> dict:
    return {
        "id": uuid4(),
        "name": name,
        "display_name": name.title(),
        "content": f"# {name} skill content",
        "token_cost": token_cost,
        "priority": priority,
        "trigger_keywords": keywords or [],
        "compatible_tools": tools or [],
        "compatible_roles": roles or [],
        "is_pinned": pinned,
        "is_active": True,
    }


def _make_selector(rows: list[dict], token_budget: int = 2000) -> SkillSelector:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    selector = SkillSelector(pool, token_budget=token_budget)
    return selector


# ------------------------------------------------------------------
# Selection tests
# ------------------------------------------------------------------

class TestSelect:

    @pytest.mark.asyncio
    async def test_empty_skills(self) -> None:
        sel = _make_selector([])
        result = await sel.select("any prompt", "executor")
        assert result == []

    @pytest.mark.asyncio
    async def test_keyword_match(self) -> None:
        rows = [_skill_row("python", keywords=["python", "code"])]
        sel = _make_selector(rows)
        result = await sel.select("write python code", "executor")
        assert len(result) == 1
        assert result[0].name == "python"
        assert result[0].match_reason == "keyword"

    @pytest.mark.asyncio
    async def test_tool_match(self) -> None:
        rows = [_skill_row("search", tools=["web_search"])]
        sel = _make_selector(rows)
        result = await sel.select("find information", "executor", available_tools=["web_search"])
        assert len(result) == 1
        assert result[0].match_reason == "tool"

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        rows = [_skill_row("python", keywords=["python"], priority=0)]
        sel = _make_selector(rows)
        result = await sel.select("cook dinner", "executor")
        assert result == []

    @pytest.mark.asyncio
    async def test_inactive_skill_excluded(self) -> None:
        """Skills with ``is_active=False`` must never appear in results."""
        row = _skill_row("python", keywords=["python"])
        row["is_active"] = False
        sel = _make_selector([row])
        result = await sel.select("write python code", "executor")
        assert result == []

    @pytest.mark.asyncio
    async def test_pinned_always_included(self) -> None:
        rows = [_skill_row("pinned-skill", pinned=True)]
        sel = _make_selector(rows)
        result = await sel.select("irrelevant prompt", "executor")
        assert len(result) == 1
        assert result[0].match_reason == "pinned"

    @pytest.mark.asyncio
    async def test_token_budget_trims(self) -> None:
        rows = [
            _skill_row("big", keywords=["test"], token_cost=1500, priority=10),
            _skill_row("small", keywords=["test"], token_cost=600, priority=5),
        ]
        sel = _make_selector(rows, token_budget=2000)
        result = await sel.select("test prompt", "executor")
        # big (1500) fits, small (1500+600=2100) exceeds budget
        assert len(result) == 1
        assert result[0].name == "big"

    @pytest.mark.asyncio
    async def test_pinned_not_affected_by_budget(self) -> None:
        rows = [
            _skill_row("pinned", pinned=True, token_cost=1500),
            _skill_row("extra", keywords=["test"], token_cost=600),
        ]
        sel = _make_selector(rows, token_budget=2000)
        result = await sel.select("test prompt", "executor")
        # Pinned uses 1500, extra fits (1500+600 = 2100 > 2000)
        # But pinned always included, extra trimmed
        assert len(result) == 1
        assert result[0].match_reason == "pinned"

    @pytest.mark.asyncio
    async def test_priority_ordering(self) -> None:
        rows = [
            _skill_row("low", keywords=["code"], priority=10, token_cost=100),
            _skill_row("high", keywords=["code"], priority=90, token_cost=100),
        ]
        sel = _make_selector(rows, token_budget=2000)
        result = await sel.select("write code", "executor")
        # Both should be returned; higher priority first
        assert len(result) == 2
        assert result[0].name == "high"

    @pytest.mark.asyncio
    async def test_role_match_with_priority(self) -> None:
        """Skills with positive priority are included even without keyword match."""
        rows = [_skill_row("general", roles=["executor"], priority=50)]
        sel = _make_selector(rows)
        result = await sel.select("do something", "executor")
        # priority=50 gives total_score=50 > 0, so it is included
        assert len(result) == 1
        assert result[0].match_reason == "role"

    @pytest.mark.asyncio
    async def test_multiple_matches_sorted(self) -> None:
        rows = [
            _skill_row("a", keywords=["data"], priority=30, token_cost=100),
            _skill_row("b", keywords=["data", "analysis"], priority=50, token_cost=100),
        ]
        sel = _make_selector(rows, token_budget=2000)
        result = await sel.select("do data analysis", "executor")
        assert len(result) == 2
        # 'b' has more keyword overlap + higher priority → first
        assert result[0].name == "b"


# ------------------------------------------------------------------
# SelectedSkill model
# ------------------------------------------------------------------

class TestSelectedSkill:

    def test_to_dict(self) -> None:
        s = SelectedSkill(
            skill_id="s1", name="test", display_name="Test",
            content="body", token_cost=50, priority=10, match_reason="keyword",
        )
        d = s.to_dict()
        assert d["skill_id"] == "s1"
        assert d["match_reason"] == "keyword"
        assert "content" not in d  # content excluded from dict


# ------------------------------------------------------------------
# Token budget property
# ------------------------------------------------------------------

class TestTokenBudget:

    def test_default_budget(self) -> None:
        pool = AsyncMock()
        sel = SkillSelector(pool)
        assert sel.token_budget == 2000

    def test_custom_budget(self) -> None:
        pool = AsyncMock()
        sel = SkillSelector(pool, token_budget=500)
        assert sel.token_budget == 500


# ------------------------------------------------------------------
# Log usage
# ------------------------------------------------------------------

class TestLogUsage:

    @pytest.mark.asyncio
    async def test_log_usage_calls_pool(self) -> None:
        pool = AsyncMock()
        sel = SkillSelector(pool)
        await sel.log_usage("s1", "executor", "test task", True, 42)
        pool.execute.assert_awaited_once()
