"""Knowledge repository — CRUD for knowledge_bases, knowledge_sources.

All reads and writes go through the async DB pool (asyncpg or SqlitePool).
The global TF-IDF KnowledgeBase is still used for the actual vector store,
but **metadata** (bases, sources, config) is persisted in the relational DB.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class KnowledgeRepository:
    """Async repository for knowledge_bases and knowledge_sources tables."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ── Knowledge Bases ──────────────────────────────────────

    async def list_bases(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, description, embedding_model, created_at, updated_at "
                "FROM dbo.knowledge_bases ORDER BY created_at",
            )
        return [_parse_base_row(r) for r in rows]

    async def get_base(self, base_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, embedding_model, created_at, updated_at "
                "FROM dbo.knowledge_bases WHERE id = $1",
                base_id,
            )
        if row is None:
            return None
        return _parse_base_row(row)

    async def get_base_by_name(self, name: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, embedding_model, created_at, updated_at "
                "FROM dbo.knowledge_bases WHERE name = $1",
                name,
            )
        if row is None:
            return None
        return _parse_base_row(row)

    async def create_base(
        self,
        *,
        name: str,
        description: str = "",
        embedding_model: str | None = None,
        base_id: str | None = None,
    ) -> dict[str, Any]:
        bid = base_id or str(uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dbo.knowledge_bases (id, name, description, embedding_model, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, now(), now())""",
                bid,
                name,
                description,
                embedding_model,
            )
        return {"id": bid, "name": name, "description": description,
                "embedding_model": embedding_model}

    async def update_base(self, base_id: str, **kwargs: Any) -> dict[str, Any] | None:
        base = await self.get_base(base_id)
        if base is None:
            return None

        sets: list[str] = []
        params: list[Any] = []
        idx = 1
        for key in ("name", "description", "embedding_model"):
            if key in kwargs:
                sets.append(f"{key} = ${idx}")
                params.append(kwargs[key])
                idx += 1

        if not sets:
            return base

        sets.append(f"updated_at = now()")
        params.append(base_id)
        sql = f"UPDATE dbo.knowledge_bases SET {', '.join(sets)} WHERE id = ${idx}"

        async with self._pool.acquire() as conn:
            await conn.execute(sql, *params)
        return await self.get_base(base_id)

    async def delete_base(self, base_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM dbo.knowledge_bases WHERE id = $1", base_id,
            )
        # asyncpg returns 'DELETE N', SqlitePool returns similar
        return "0" not in str(result)

    # ── Knowledge Sources ────────────────────────────────────

    async def list_sources(self, base_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, base_id, file_path, status, indexed_at, created_at "
                "FROM dbo.knowledge_sources WHERE base_id = $1 ORDER BY created_at",
                base_id,
            )
        return [_parse_source_row(r) for r in rows]

    async def add_source(
        self,
        base_id: str,
        file_path: str,
        *,
        status: str = "pending",
        source_id: str | None = None,
    ) -> dict[str, Any]:
        sid = source_id or str(uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dbo.knowledge_sources (id, base_id, file_path, status, created_at)
                   VALUES ($1, $2, $3, $4, now())""",
                sid,
                base_id,
                file_path,
                status,
            )
        return {"id": sid, "base_id": base_id, "file_path": file_path, "status": status}

    async def update_source_status(
        self,
        source_id: str,
        status: str,
        *,
        indexed_at_now: bool = False,
    ) -> None:
        if indexed_at_now:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE dbo.knowledge_sources SET status = $1, indexed_at = now() WHERE id = $2",
                    status,
                    source_id,
                )
        else:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE dbo.knowledge_sources SET status = $1 WHERE id = $2",
                    status,
                    source_id,
                )

    async def remove_source(self, source_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM dbo.knowledge_sources WHERE id = $1", source_id,
            )
        return "0" not in str(result)

    # ── Seed helper ──────────────────────────────────────────

    async def ensure_global_base(self) -> str:
        """Ensure a 'Global Knowledge Base' row exists. Return its id."""
        existing = await self.get_base_by_name("Global Knowledge Base")
        if existing:
            return existing["id"]
        base = await self.create_base(
            name="Global Knowledge Base",
            description="Default TF-IDF knowledge base",
            base_id="global",
        )
        return base["id"]


# ── Row parsers ──────────────────────────────────────────────

def _parse_base_row(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"] or "",
        "embedding_model": row["embedding_model"],
        "created_at": _dt(row["created_at"]),
        "updated_at": _dt(row["updated_at"]),
    }


def _parse_source_row(row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "base_id": str(row["base_id"]),
        "path": row["file_path"],
        "status": row["status"],
        "indexed_at": _dt(row["indexed_at"]),
        "created_at": _dt(row["created_at"]),
    }


def _dt(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)
