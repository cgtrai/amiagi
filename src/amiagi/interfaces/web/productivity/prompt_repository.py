"""Prompt repository — CRUD for shared prompt templates.

Table: ``dbo.prompts`` — user-created prompt templates with tags,
use-count tracking, and parameter support ({placeholder} syntax).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\{(\w+)\}")


@dataclass
class PromptRecord:
    id: str
    user_id: str
    title: str
    template: str
    tags: list[str]
    is_public: bool
    use_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "template": self.template,
            "tags": self.tags,
            "is_public": self.is_public,
            "use_count": self.use_count,
            "parameters": self.parameters,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def parameters(self) -> list[str]:
        """Extract {placeholder} names from the template."""
        return _PARAM_RE.findall(self.template)

    def render(self, values: dict[str, str]) -> str:
        """Substitute {placeholder} with values."""
        result = self.template
        for key, val in values.items():
            result = result.replace(f"{{{key}}}", val)
        return result


def _row_to_prompt(row) -> PromptRecord:
    return PromptRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        title=row["title"],
        template=row["template"],
        tags=list(row["tags"] or []),
        is_public=row["is_public"],
        use_count=row["use_count"],
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class PromptRepository:
    """CRUD for dbo.prompts."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def list_prompts(
        self,
        *,
        user_id: str | None = None,
        tag: str | None = None,
        public_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if public_only:
            conditions.append("is_public = true")
        if user_id:
            conditions.append(f"(user_id = ${idx}::uuid OR is_public = true)")
            params.append(user_id)
            idx += 1
        if tag:
            conditions.append(f"${idx} = ANY(tags)")
            params.append(tag)
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT * FROM dbo.prompts {where}
            ORDER BY use_count DESC, created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        params.extend([limit, offset])
        rows = await self._pool.fetch(sql, *params)
        return [_row_to_prompt(r) for r in rows]

    async def get_prompt(self, prompt_id: str) -> PromptRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.prompts WHERE id = $1::uuid", prompt_id
        )
        return _row_to_prompt(row) if row else None

    async def create_prompt(
        self, *, user_id: str, title: str, template: str,
        tags: list[str] | None = None, is_public: bool = False,
    ) -> PromptRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.prompts (user_id, title, template, tags, is_public)
            VALUES ($1::uuid, $2, $3, $4, $5)
            RETURNING *
            """,
            user_id, title, template, tags or [], is_public,
        )
        return _row_to_prompt(row)

    async def update_prompt(
        self, prompt_id: str, **fields,
    ) -> PromptRecord | None:
        allowed = {"title", "template", "tags", "is_public"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return await self.get_prompt(prompt_id)

        set_parts: list[str] = []
        params: list[Any] = []
        idx = 1
        for k, v in updates.items():
            set_parts.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1
        set_parts.append(f"updated_at = now()")
        params.append(prompt_id)
        sql = f"""
            UPDATE dbo.prompts SET {', '.join(set_parts)}
            WHERE id = ${idx}::uuid RETURNING *
        """
        row = await self._pool.fetchrow(sql, *params)
        return _row_to_prompt(row) if row else None

    async def delete_prompt(self, prompt_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.prompts WHERE id = $1::uuid", prompt_id
        )
        return result.endswith("1")

    async def increment_use_count(self, prompt_id: str) -> None:
        await self._pool.execute(
            "UPDATE dbo.prompts SET use_count = use_count + 1 WHERE id = $1::uuid",
            prompt_id,
        )
