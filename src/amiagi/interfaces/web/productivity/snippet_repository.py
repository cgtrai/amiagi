"""Snippet repository — save & manage code/text snippets from agent output.

Table: ``dbo.snippets`` — user-saved fragments with source context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class SnippetRecord:
    id: str
    user_id: str
    content: str
    tags: list[str]
    source_agent: str | None
    source_task_id: str | None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "content": self.content,
            "tags": self.tags,
            "source_agent": self.source_agent,
            "source_task_id": self.source_task_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_snippet(row) -> SnippetRecord:
    return SnippetRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        content=row["content"],
        tags=list(row["tags"] or []),
        source_agent=row.get("source_agent"),
        source_task_id=row.get("source_task_id"),
        created_at=row.get("created_at"),
    )


class SnippetRepository:
    """CRUD for dbo.snippets."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def list_snippets(
        self, user_id: str, *, tag: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[SnippetRecord]:
        conditions = ["user_id = $1::uuid"]
        params: list[Any] = [user_id]
        idx = 2
        if tag:
            conditions.append(f"${idx} = ANY(tags)")
            params.append(tag)
            idx += 1
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        sql = f"""
            SELECT * FROM dbo.snippets WHERE {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await self._pool.fetch(sql, *params)
        return [_row_to_snippet(r) for r in rows]

    async def get_snippet(self, snippet_id: str) -> SnippetRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.snippets WHERE id = $1::uuid", snippet_id
        )
        return _row_to_snippet(row) if row else None

    async def create_snippet(
        self, *, user_id: str, content: str, tags: list[str] | None = None,
        source_agent: str | None = None, source_task_id: str | None = None,
    ) -> SnippetRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.snippets (user_id, content, tags, source_agent, source_task_id)
            VALUES ($1::uuid, $2, $3, $4, $5)
            RETURNING *
            """,
            user_id, content, tags or [], source_agent, source_task_id,
        )
        return _row_to_snippet(row)

    async def delete_snippet(self, snippet_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.snippets WHERE id = $1::uuid", snippet_id
        )
        return result.endswith("1")
