"""Skill & Trait repository — PostgreSQL CRUD for skills and agent traits.

Thin layer over asyncpg for the ``dbo.skills``, ``dbo.agent_traits``,
``dbo.agent_skill_assignments``, and ``dbo.skill_usage_log`` tables.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class SkillRecord:
    """In-memory representation of a skill row."""

    id: str
    name: str
    display_name: str
    category: str
    description: str
    content: str
    trigger_keywords: list[str]
    compatible_tools: list[str]
    compatible_roles: list[str]
    token_cost: int
    priority: int
    is_active: bool
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "description": self.description,
            "content": self.content,
            "trigger_keywords": self.trigger_keywords,
            "compatible_tools": self.compatible_tools,
            "compatible_roles": self.compatible_roles,
            "token_cost": self.token_cost,
            "priority": self.priority,
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class TraitRecord:
    """In-memory representation of an agent trait row."""

    id: str
    trait_type: str
    agent_role: str
    name: str
    content: str
    token_cost: int
    priority: int
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trait_type": self.trait_type,
            "agent_role": self.agent_role,
            "name": self.name,
            "content": self.content,
            "token_cost": self.token_cost,
            "priority": self.priority,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SkillRepository:
    """CRUD operations for skills and agent traits."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ── Skills CRUD ────────────────────────────────────────────

    async def list_skills(
        self,
        *,
        category: str | None = None,
        role: str | None = None,
        active_only: bool = True,
    ) -> list[SkillRecord]:
        """List skills with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if active_only:
            conditions.append("is_active = true")
        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if role:
            conditions.append(f"${idx} = ANY(compatible_roles)")
            params.append(role)
            idx += 1

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = await self._pool.fetch(
            f"SELECT * FROM dbo.skills {where} ORDER BY priority DESC, name",
            *params,
        )
        return [self._row_to_skill(r) for r in rows]

    async def get_skill(self, skill_id: str) -> SkillRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.skills WHERE id = $1::uuid", skill_id,
        )
        return self._row_to_skill(row) if row else None

    async def create_skill(self, **kwargs: Any) -> SkillRecord:
        skill_id = str(uuid4())
        now = datetime.now(timezone.utc)
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.skills (id, name, display_name, category, description, content,
                trigger_keywords, compatible_tools, compatible_roles, token_cost, priority,
                is_active, version, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING *
            """,
            skill_id,
            kwargs["name"],
            kwargs.get("display_name", kwargs["name"]),
            kwargs.get("category", "general"),
            kwargs.get("description", ""),
            kwargs["content"],
            kwargs.get("trigger_keywords", []),
            kwargs.get("compatible_tools", []),
            kwargs.get("compatible_roles", []),
            kwargs.get("token_cost", 0),
            kwargs.get("priority", 50),
            kwargs.get("is_active", True),
            kwargs.get("version", 1),
            now,
            now,
        )
        return self._row_to_skill(row)

    async def update_skill(self, skill_id: str, **kwargs: Any) -> SkillRecord | None:
        existing = await self.get_skill(skill_id)
        if not existing:
            return None

        updates: list[str] = []
        params: list[Any] = []
        idx = 1

        for key in ("name", "display_name", "category", "description", "content",
                     "trigger_keywords", "compatible_tools", "compatible_roles",
                     "token_cost", "priority", "is_active"):
            if key in kwargs:
                updates.append(f"{key} = ${idx}")
                params.append(kwargs[key])
                idx += 1

        if not updates:
            return existing

        updates.append(f"updated_at = ${idx}")
        params.append(datetime.now(timezone.utc))
        idx += 1
        updates.append(f"version = version + 1")
        params.append(skill_id)

        row = await self._pool.fetchrow(
            f"UPDATE dbo.skills SET {', '.join(updates)} WHERE id = ${idx}::uuid RETURNING *",
            *params,
        )
        return self._row_to_skill(row) if row else None

    async def delete_skill(self, skill_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.skills WHERE id = $1::uuid", skill_id,
        )
        return result.endswith("1")

    # ── Traits CRUD ────────────────────────────────────────────

    async def list_traits(
        self,
        *,
        trait_type: str | None = None,
        agent_role: str | None = None,
        active_only: bool = True,
    ) -> list[TraitRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if active_only:
            conditions.append("is_active = true")
        if trait_type:
            conditions.append(f"trait_type = ${idx}")
            params.append(trait_type)
            idx += 1
        if agent_role:
            conditions.append(f"agent_role = ${idx}")
            params.append(agent_role)
            idx += 1

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = await self._pool.fetch(
            f"SELECT * FROM dbo.agent_traits {where} ORDER BY priority DESC, name",
            *params,
        )
        return [self._row_to_trait(r) for r in rows]

    async def get_trait(self, trait_id: str) -> TraitRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.agent_traits WHERE id = $1::uuid", trait_id,
        )
        return self._row_to_trait(row) if row else None

    async def create_trait(self, **kwargs: Any) -> TraitRecord:
        trait_id = str(uuid4())
        now = datetime.now(timezone.utc)
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.agent_traits (id, trait_type, agent_role, name, content,
                token_cost, priority, is_active, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            trait_id,
            kwargs["trait_type"],
            kwargs["agent_role"],
            kwargs["name"],
            kwargs["content"],
            kwargs.get("token_cost", 0),
            kwargs.get("priority", 50),
            kwargs.get("is_active", True),
            now,
            now,
        )
        return self._row_to_trait(row)

    async def update_trait(self, trait_id: str, **kwargs: Any) -> TraitRecord | None:
        existing = await self.get_trait(trait_id)
        if not existing:
            return None

        updates: list[str] = []
        params: list[Any] = []
        idx = 1

        for key in ("name", "content", "token_cost", "priority", "is_active"):
            if key in kwargs:
                updates.append(f"{key} = ${idx}")
                params.append(kwargs[key])
                idx += 1

        if not updates:
            return existing

        updates.append(f"updated_at = ${idx}")
        params.append(datetime.now(timezone.utc))
        idx += 1
        params.append(trait_id)

        row = await self._pool.fetchrow(
            f"UPDATE dbo.agent_traits SET {', '.join(updates)} WHERE id = ${idx}::uuid RETURNING *",
            *params,
        )
        return self._row_to_trait(row) if row else None

    async def delete_trait(self, trait_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.agent_traits WHERE id = $1::uuid", trait_id,
        )
        return result.endswith("1")

    # ── Skill assignments ──────────────────────────────────────

    async def assign_skill(self, agent_role: str, skill_id: str, *, is_pinned: bool = False) -> None:
        await self._pool.execute(
            """
            INSERT INTO dbo.agent_skill_assignments (agent_role, skill_id, is_pinned)
            VALUES ($1, $2::uuid, $3)
            ON CONFLICT (agent_role, skill_id) DO UPDATE SET is_pinned = EXCLUDED.is_pinned
            """,
            agent_role, skill_id, is_pinned,
        )

    async def get_pinned_skills(self, agent_role: str) -> list[str]:
        rows = await self._pool.fetch(
            "SELECT skill_id FROM dbo.agent_skill_assignments WHERE agent_role = $1 AND is_pinned = true",
            agent_role,
        )
        return [str(r["skill_id"]) for r in rows]

    # ── Usage logging ──────────────────────────────────────────

    async def log_usage(
        self,
        skill_id: str,
        agent_role: str,
        task_summary: str = "",
        was_useful: bool | None = None,
        tokens_used: int = 0,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO dbo.skill_usage_log (skill_id, agent_role, task_summary, was_useful, tokens_used)
            VALUES ($1::uuid, $2, $3, $4, $5)
            """,
            skill_id, agent_role, task_summary, was_useful, tokens_used,
        )

    async def skill_usage_stats(self, skill_id: str) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            """
            SELECT count(*) as total_uses,
                   count(*) FILTER (WHERE was_useful = true) as useful_count,
                   coalesce(sum(tokens_used), 0) as total_tokens
            FROM dbo.skill_usage_log WHERE skill_id = $1::uuid
            """,
            skill_id,
        )
        if not row:
            return {"total_uses": 0, "useful_count": 0, "total_tokens": 0}
        return dict(row)

    async def skill_usage_map(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT s.id,
                   s.name,
                   s.display_name,
                   coalesce(l.agent_role, 'unassigned') as agent_role,
                   count(l.skill_id) as total_uses,
                   count(*) FILTER (WHERE l.was_useful = true) as useful_count,
                   coalesce(sum(l.tokens_used), 0) as total_tokens
            FROM dbo.skills s
            LEFT JOIN dbo.skill_usage_log l ON l.skill_id = s.id
            GROUP BY s.id, s.name, s.display_name, l.agent_role
            ORDER BY s.name, total_uses DESC, agent_role
            """
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            skill_id = str(row["id"])
            item = grouped.setdefault(skill_id, {
                "skill_id": skill_id,
                "name": row["name"],
                "display_name": row.get("display_name") or row["name"],
                "total_uses": 0,
                "useful_count": 0,
                "total_tokens": 0,
                "agents": [],
            })
            uses = int(row.get("total_uses") or 0)
            useful = int(row.get("useful_count") or 0)
            tokens = int(row.get("total_tokens") or 0)
            item["total_uses"] += uses
            item["useful_count"] += useful
            item["total_tokens"] += tokens
            if uses > 0:
                item["agents"].append({
                    "agent_role": row.get("agent_role") or "unassigned",
                    "total_uses": uses,
                    "useful_count": useful,
                    "total_tokens": tokens,
                })
        return sorted(grouped.values(), key=lambda item: (-item["total_uses"], item["name"]))

    # ── Row mappers ────────────────────────────────────────────

    @staticmethod
    def _row_to_skill(row) -> SkillRecord:
        return SkillRecord(
            id=str(row["id"]),
            name=row["name"],
            display_name=row["display_name"],
            category=row["category"],
            description=row.get("description", ""),
            content=row["content"],
            trigger_keywords=list(row.get("trigger_keywords") or []),
            compatible_tools=list(row.get("compatible_tools") or []),
            compatible_roles=list(row.get("compatible_roles") or []),
            token_cost=row["token_cost"],
            priority=row["priority"],
            is_active=row["is_active"],
            version=row.get("version", 1),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _row_to_trait(row) -> TraitRecord:
        return TraitRecord(
            id=str(row["id"]),
            trait_type=row["trait_type"],
            agent_role=row["agent_role"],
            name=row["name"],
            content=row["content"],
            token_cost=row["token_cost"],
            priority=row["priority"],
            is_active=row["is_active"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
