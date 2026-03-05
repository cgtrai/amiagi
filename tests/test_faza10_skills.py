"""Tests for Faza 10 — DB-driven Skill & Trait Management.

Covers audit criteria 10.1–10.11:
- 10.1: Migration 002_skills.sql exists with correct tables
- 10.2: SkillSelector.select() returns max N skills
- 10.3: Token budget respected
- 10.7: Web GUI CRUD skills
- 10.8: Agent traits CRUD with filters
- 10.10: skill_usage_log records usage
- 10.11: Tests ≥15 for skill_selector, ≥10 for skill_repository
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# 10.1 — Migration file exists and has correct structure
# ---------------------------------------------------------------------------

class TestMigrationFile:
    """10.1: 002_skills.sql creates tables in dbo schema."""

    def test_migration_file_exists(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        assert path.exists()

    def test_migration_creates_skills_table(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.skills" in content

    def test_migration_creates_traits_table(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.agent_traits" in content

    def test_migration_creates_assignments_table(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.agent_skill_assignments" in content

    def test_migration_creates_usage_log_table(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.skill_usage_log" in content

    def test_migration_has_gin_indexes(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "USING GIN(trigger_keywords)" in content
        assert "USING GIN(compatible_roles)" in content

    def test_migration_dbo_schema(self):
        path = Path(__file__).parent.parent / "src/amiagi/interfaces/web/db/migrations/002_skills.sql"
        content = path.read_text()
        assert "SET search_path TO dbo" in content


# ---------------------------------------------------------------------------
# 10.2 + 10.3 — SkillSelector
# ---------------------------------------------------------------------------

def _make_skill_row(
    skill_id="s1", name="test_skill", display_name="Test Skill",
    content="Test content", keywords=None, tools=None, roles=None,
    token_cost=100, priority=50, is_pinned=False,
):
    """Create a mock DB row for SkillSelector."""
    return {
        "id": skill_id,
        "name": name,
        "display_name": display_name,
        "content": content,
        "trigger_keywords": keywords or [],
        "compatible_tools": tools or [],
        "compatible_roles": roles or [],
        "token_cost": token_cost,
        "priority": priority,
        "is_active": True,
        "is_pinned": is_pinned,
    }


class TestSkillSelector:
    """10.2: SkillSelector.select() returns bounded results."""

    @pytest.mark.asyncio
    async def test_select_empty_db(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("test prompt", "executor")
        assert result == []

    @pytest.mark.asyncio
    async def test_select_keyword_match(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [_make_skill_row(keywords=["review", "code"], token_cost=100)]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("please review the code", "executor")
        assert len(result) == 1
        assert result[0].match_reason == "keyword"

    @pytest.mark.asyncio
    async def test_select_tool_match(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [_make_skill_row(tools=["shell"], token_cost=100)]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("run something", "executor", available_tools=["shell"])
        assert len(result) == 1
        assert result[0].match_reason == "tool"

    @pytest.mark.asyncio
    async def test_select_pinned_always_included(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [_make_skill_row(is_pinned=True, token_cost=500)]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("anything", "executor")
        assert len(result) == 1
        assert result[0].match_reason == "pinned"

    @pytest.mark.asyncio
    async def test_token_budget_respected(self):
        """10.3: Token budget enforced."""
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [
            _make_skill_row(skill_id="s1", keywords=["review"], token_cost=800, priority=90),
            _make_skill_row(skill_id="s2", keywords=["review"], token_cost=800, priority=80),
            _make_skill_row(skill_id="s3", keywords=["review"], token_cost=800, priority=70),
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=1500)
        result = await sel.select("review this", "executor")
        total_tokens = sum(s.token_cost for s in result)
        assert total_tokens <= 1500
        assert len(result) <= 2  # Only 2 fit in 1500

    @pytest.mark.asyncio
    async def test_select_max_3_typical(self):
        """10.2: Typical prompt returns max 3 skills."""
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [
            _make_skill_row(skill_id=f"s{i}", keywords=["code"], token_cost=300, priority=50-i)
            for i in range(5)
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=1000)
        result = await sel.select("review the code", "executor")
        assert len(result) <= 3  # Budget 1000 / 300 per skill = 3 max

    @pytest.mark.asyncio
    async def test_select_priority_ordering(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [
            _make_skill_row(skill_id="low", keywords=["test"], token_cost=100, priority=10),
            _make_skill_row(skill_id="high", keywords=["test"], token_cost=100, priority=90),
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=150)
        result = await sel.select("run test", "executor")
        assert len(result) == 1
        assert result[0].skill_id == "high"

    @pytest.mark.asyncio
    async def test_select_no_keyword_match_uses_role(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [_make_skill_row(keywords=["security"], token_cost=100, priority=50)]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("hello world", "executor")
        # No keyword match, but priority > 0 → role-based inclusion
        assert len(result) == 1
        assert result[0].match_reason == "role"

    @pytest.mark.asyncio
    async def test_select_zero_priority_no_match_empty(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [_make_skill_row(keywords=["security"], token_cost=100, priority=0)]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=2000)
        result = await sel.select("hello world", "executor")
        # No keyword/tool match and priority=0 → total_score=0 → excluded
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_log_usage(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        pool = MagicMock()
        pool.execute = AsyncMock()
        sel = SkillSelector(pool)
        await sel.log_usage("skill-id", "executor", "summarize code", True, 150)
        pool.execute.assert_called_once()
        sql = pool.execute.call_args[0][0]
        assert "skill_usage_log" in sql

    def test_token_budget_property(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        pool = MagicMock()
        sel = SkillSelector(pool, token_budget=3000)
        assert sel.token_budget == 3000

    def test_selected_skill_to_dict(self):
        from amiagi.interfaces.web.skills.skill_selector import SelectedSkill
        s = SelectedSkill(
            skill_id="s1", name="test", display_name="Test",
            content="content", token_cost=100, priority=50, match_reason="keyword",
        )
        d = s.to_dict()
        assert d["skill_id"] == "s1"
        assert d["match_reason"] == "keyword"

    @pytest.mark.asyncio
    async def test_pinned_skills_bypass_budget_check(self):
        """Pinned skills always included even if they push past budget."""
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [
            _make_skill_row(skill_id="pinned", is_pinned=True, token_cost=1500),
            _make_skill_row(skill_id="normal", keywords=["test"], token_cost=100),
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=1000)
        result = await sel.select("test", "executor")
        # Pinned should be included despite exceeding budget
        pinned = [s for s in result if s.skill_id == "pinned"]
        assert len(pinned) == 1

    @pytest.mark.asyncio
    async def test_multiple_keyword_matches_scored_higher(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        rows = [
            _make_skill_row(skill_id="single", keywords=["review"], token_cost=100, priority=50),
            _make_skill_row(skill_id="multi", keywords=["review", "code", "test"], token_cost=100, priority=50),
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        sel = SkillSelector(pool, token_budget=150)
        result = await sel.select("review the code and test it", "executor")
        # Only room for one — multi should win due to more keyword matches
        assert len(result) == 1
        assert result[0].skill_id == "multi"

    @pytest.mark.asyncio
    async def test_default_budget(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        pool = MagicMock()
        sel = SkillSelector(pool)
        assert sel.token_budget == 2000


# ---------------------------------------------------------------------------
# 10.7 + 10.8 — SkillRepository (CRUD)
# ---------------------------------------------------------------------------

class TestSkillRepository:
    """10.7/10.8: Skill and trait CRUD."""

    def test_skill_record_to_dict(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRecord
        s = SkillRecord(
            id="s1", name="test", display_name="Test", category="general",
            description="desc", content="content",
            trigger_keywords=["kw"], compatible_tools=["tool"],
            compatible_roles=["exec"], token_cost=100, priority=50,
            is_active=True, version=1,
        )
        d = s.to_dict()
        assert d["id"] == "s1"
        assert d["trigger_keywords"] == ["kw"]

    def test_trait_record_to_dict(self):
        from amiagi.interfaces.web.skills.skill_repository import TraitRecord
        t = TraitRecord(
            id="t1", trait_type="persona", agent_role="executor",
            name="friendly", content="Be friendly", token_cost=50,
            priority=80, is_active=True,
        )
        d = t.to_dict()
        assert d["trait_type"] == "persona"
        assert d["agent_role"] == "executor"

    @pytest.mark.asyncio
    async def test_list_skills_builds_query(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = SkillRepository(pool)
        await repo.list_skills(category="coding", role="executor")
        sql = pool.fetch.call_args[0][0]
        assert "dbo.skills" in sql
        assert "category = $1" in sql

    @pytest.mark.asyncio
    async def test_get_skill(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        repo = SkillRepository(pool)
        result = await repo.get_skill("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_skill(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        mock_row = {
            "id": "new-id", "name": "test", "display_name": "Test",
            "category": "general", "description": "", "content": "content",
            "trigger_keywords": [], "compatible_tools": [], "compatible_roles": [],
            "token_cost": 0, "priority": 50, "is_active": True, "version": 1,
            "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)
        repo = SkillRepository(pool)
        result = await repo.create_skill(name="test", content="content")
        assert result.name == "test"

    @pytest.mark.asyncio
    async def test_delete_skill(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        repo = SkillRepository(pool)
        assert await repo.delete_skill("some-id") is True

    @pytest.mark.asyncio
    async def test_delete_skill_not_found(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        repo = SkillRepository(pool)
        assert await repo.delete_skill("nope") is False

    @pytest.mark.asyncio
    async def test_list_traits(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = SkillRepository(pool)
        await repo.list_traits(trait_type="persona", agent_role="executor")
        sql = pool.fetch.call_args[0][0]
        assert "agent_traits" in sql
        assert "trait_type = $1" in sql

    @pytest.mark.asyncio
    async def test_assign_skill(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.execute = AsyncMock()
        repo = SkillRepository(pool)
        await repo.assign_skill("executor", "skill-id", is_pinned=True)
        sql = pool.execute.call_args[0][0]
        assert "agent_skill_assignments" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_get_pinned_skills(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[{"skill_id": "s1"}, {"skill_id": "s2"}])
        repo = SkillRepository(pool)
        result = await repo.get_pinned_skills("executor")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_log_usage(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.execute = AsyncMock()
        repo = SkillRepository(pool)
        await repo.log_usage("s1", "executor", "test task", True, 100)
        sql = pool.execute.call_args[0][0]
        assert "skill_usage_log" in sql

    @pytest.mark.asyncio
    async def test_skill_usage_stats(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"total_uses": 5, "useful_count": 3, "total_tokens": 500})
        repo = SkillRepository(pool)
        stats = await repo.skill_usage_stats("s1")
        assert stats["total_uses"] == 5


# ---------------------------------------------------------------------------
# 10.7 — Web GUI CRUD routes
# ---------------------------------------------------------------------------

class TestSkillAdminRoutes:
    """10.7: Web GUI CRUD skills routes."""

    def test_skill_admin_routes_exist(self):
        from amiagi.interfaces.web.routes.skill_admin_routes import skill_admin_routes
        paths = [r.path for r in skill_admin_routes]
        assert "/admin/skills" in paths
        assert "/admin/skills/{id}" in paths
        assert "/admin/skills/{id}/stats" in paths
        assert "/admin/traits" in paths
        assert "/admin/traits/{id}" in paths

    def test_skills_routes_wired_in_app(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "skill_admin_routes" in source
        assert "skill_repository" in source
        assert "skill_selector" in source


# ---------------------------------------------------------------------------
# 10.10 — skill_usage_log registration
# ---------------------------------------------------------------------------

class TestSkillUsageLog:
    """10.10: Usage log records."""

    @pytest.mark.asyncio
    async def test_selector_log_usage_writes(self):
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector
        pool = MagicMock()
        pool.execute = AsyncMock()
        sel = SkillSelector(pool)
        await sel.log_usage("s1", "executor", "summary", True, 200)
        assert pool.execute.called
        sql = pool.execute.call_args[0][0]
        assert "skill_usage_log" in sql

    @pytest.mark.asyncio
    async def test_repo_log_usage_writes(self):
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        pool = MagicMock()
        pool.execute = AsyncMock()
        repo = SkillRepository(pool)
        await repo.log_usage("s1", "executor", "task", None, 100)
        assert pool.execute.called
