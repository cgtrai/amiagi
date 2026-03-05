"""Tests for SkillRepository — CRUD operations for skills and traits."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from amiagi.interfaces.web.skills.skill_repository import (
    SkillRecord,
    SkillRepository,
    TraitRecord,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _skill_row(**overrides) -> dict:
    base = {
        "id": uuid4(),
        "name": "python_basics",
        "display_name": "Python Basics",
        "category": "programming",
        "description": "Basic Python skill",
        "content": "# Python basics...",
        "trigger_keywords": ["python", "code"],
        "compatible_tools": ["code_runner"],
        "compatible_roles": ["executor"],
        "token_cost": 100,
        "priority": 50,
        "is_active": True,
        "version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return base


def _trait_row(**overrides) -> dict:
    base = {
        "id": uuid4(),
        "trait_type": "persona",
        "agent_role": "executor",
        "name": "careful",
        "content": "Be careful with output",
        "token_cost": 30,
        "priority": 60,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return base


def _make_repo(fetch_result=None, fetchrow_result=None, fetchval_result=None, execute_result="DELETE 1") -> SkillRepository:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_result or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.execute = AsyncMock(return_value=execute_result)
    return SkillRepository(pool)


# ------------------------------------------------------------------
# Skill CRUD
# ------------------------------------------------------------------

class TestSkillCRUD:

    @pytest.mark.asyncio
    async def test_list_skills_empty(self) -> None:
        repo = _make_repo()
        result = await repo.list_skills()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_skills_returns_records(self) -> None:
        row = _skill_row()
        repo = _make_repo(fetch_result=[row])
        result = await repo.list_skills()
        assert len(result) == 1
        assert isinstance(result[0], SkillRecord)
        assert result[0].name == "python_basics"

    @pytest.mark.asyncio
    async def test_list_skills_filter_category(self) -> None:
        repo = _make_repo()
        await repo.list_skills(category="programming")
        repo._pool.fetch.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_list_skills_filter_role(self) -> None:
        repo = _make_repo()
        await repo.list_skills(role="executor")
        repo._pool.fetch.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_get_skill_found(self) -> None:
        row = _skill_row()
        repo = _make_repo(fetchrow_result=row)
        result = await repo.get_skill(str(row["id"]))
        assert result is not None
        assert result.name == "python_basics"

    @pytest.mark.asyncio
    async def test_get_skill_not_found(self) -> None:
        repo = _make_repo(fetchrow_result=None)
        result = await repo.get_skill(str(uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_create_skill(self) -> None:
        row = _skill_row()
        repo = _make_repo(fetchrow_result=row)
        result = await repo.create_skill(name="python_basics", content="# content")
        assert isinstance(result, SkillRecord)

    @pytest.mark.asyncio
    async def test_delete_skill_success(self) -> None:
        repo = _make_repo(execute_result="DELETE 1")
        assert await repo.delete_skill(str(uuid4())) is True

    @pytest.mark.asyncio
    async def test_delete_skill_not_found(self) -> None:
        repo = _make_repo(execute_result="DELETE 0")
        assert await repo.delete_skill(str(uuid4())) is False


# ------------------------------------------------------------------
# Trait CRUD
# ------------------------------------------------------------------

class TestTraitCRUD:

    @pytest.mark.asyncio
    async def test_list_traits_empty(self) -> None:
        repo = _make_repo()
        result = await repo.list_traits()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_traits_returns_records(self) -> None:
        row = _trait_row()
        repo = _make_repo(fetch_result=[row])
        result = await repo.list_traits()
        assert len(result) == 1
        assert isinstance(result[0], TraitRecord)

    @pytest.mark.asyncio
    async def test_get_trait_found(self) -> None:
        row = _trait_row()
        repo = _make_repo(fetchrow_result=row)
        result = await repo.get_trait(str(row["id"]))
        assert result is not None
        assert result.name == "careful"

    @pytest.mark.asyncio
    async def test_get_trait_not_found(self) -> None:
        repo = _make_repo(fetchrow_result=None)
        assert await repo.get_trait(str(uuid4())) is None

    @pytest.mark.asyncio
    async def test_create_trait(self) -> None:
        row = _trait_row()
        repo = _make_repo(fetchrow_result=row)
        result = await repo.create_trait(
            trait_type="persona", agent_role="executor",
            name="careful", content="Be careful",
        )
        assert isinstance(result, TraitRecord)

    @pytest.mark.asyncio
    async def test_delete_trait_success(self) -> None:
        repo = _make_repo(execute_result="DELETE 1")
        assert await repo.delete_trait(str(uuid4())) is True

    @pytest.mark.asyncio
    async def test_delete_trait_not_found(self) -> None:
        repo = _make_repo(execute_result="DELETE 0")
        assert await repo.delete_trait(str(uuid4())) is False


# ------------------------------------------------------------------
# Skill assignments
# ------------------------------------------------------------------

class TestSkillAssignments:

    @pytest.mark.asyncio
    async def test_assign_skill(self) -> None:
        repo = _make_repo()
        await repo.assign_skill("executor", str(uuid4()))
        repo._pool.execute.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_get_pinned_skills(self) -> None:
        sid = uuid4()
        repo = _make_repo(fetch_result=[{"skill_id": sid}])
        result = await repo.get_pinned_skills("executor")
        assert len(result) == 1


# ------------------------------------------------------------------
# Usage logging & stats
# ------------------------------------------------------------------

class TestUsageLog:

    @pytest.mark.asyncio
    async def test_log_usage(self) -> None:
        repo = _make_repo()
        await repo.log_usage(str(uuid4()), "executor", "test", True, 42)
        repo._pool.execute.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_skill_usage_stats(self) -> None:
        repo = _make_repo(fetchrow_result={"total_uses": 5, "useful_count": 3, "total_tokens": 200})
        stats = await repo.skill_usage_stats(str(uuid4()))
        assert stats["total_uses"] == 5
        assert stats["useful_count"] == 3

    @pytest.mark.asyncio
    async def test_skill_usage_stats_none(self) -> None:
        repo = _make_repo(fetchrow_result=None)
        stats = await repo.skill_usage_stats(str(uuid4()))
        assert stats["total_uses"] == 0


# ------------------------------------------------------------------
# Record models
# ------------------------------------------------------------------

class TestRecordModels:

    def test_skill_record_to_dict(self) -> None:
        sr = SkillRecord(
            id="s1", name="test", display_name="Test", category="general",
            description="desc", content="body", trigger_keywords=["a"],
            compatible_tools=[], compatible_roles=[], token_cost=10,
            priority=5, is_active=True, version=1,
        )
        d = sr.to_dict()
        assert d["id"] == "s1"
        assert d["name"] == "test"

    def test_trait_record_to_dict(self) -> None:
        tr = TraitRecord(
            id="t1", trait_type="persona", agent_role="exec",
            name="careful", content="body", token_cost=20,
            priority=5, is_active=True,
        )
        d = tr.to_dict()
        assert d["id"] == "t1"
        assert d["trait_type"] == "persona"
