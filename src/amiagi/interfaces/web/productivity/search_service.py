"""Global search — full-text search via PostgreSQL tsvector + GIN.

Searches across agents, tasks, files, prompts, skills, and snippets.
Uses ``dbo.search_index`` with auto-updated tsvector column.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    entity_type: str
    entity_id: str
    title: str
    snippet: str
    rank: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "title": self.title,
            "snippet": self.snippet,
            "rank": round(self.rank, 4),
        }


class SearchService:
    """Full-text search over the unified search index."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def search(
        self,
        query: str,
        *,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchResult]:
        """Search the index using PostgreSQL full-text search."""
        if not query or not query.strip():
            return []

        tsquery = " & ".join(w for w in query.split() if w)
        conditions = ["content_tsv @@ to_tsquery('english', $1)"]
        params: list[Any] = [tsquery]
        idx = 2

        if entity_type:
            conditions.append(f"entity_type = ${idx}")
            params.append(entity_type)
            idx += 1

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        sql = f"""
            SELECT entity_type, entity_id, title,
                   ts_headline('english', content, to_tsquery('english', $1),
                               'MaxFragments=2, MaxWords=30') AS snippet,
                   ts_rank(content_tsv, to_tsquery('english', $1)) AS rank
            FROM dbo.search_index
            WHERE {where}
            ORDER BY rank DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await self._pool.fetch(sql, *params)
        return [
            SearchResult(
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                title=r["title"],
                snippet=r["snippet"],
                rank=float(r["rank"]),
            )
            for r in rows
        ]

    async def index_entity(
        self,
        entity_type: str,
        entity_id: str,
        title: str,
        content: str = "",
    ) -> None:
        """Upsert an entity into the search index."""
        await self._pool.execute(
            """
            INSERT INTO dbo.search_index (entity_type, entity_id, title, content)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (entity_type, entity_id)
            DO UPDATE SET title = EXCLUDED.title,
                         content = EXCLUDED.content,
                         updated_at = now()
            """,
            entity_type, entity_id, title, content,
        )

    async def remove_entity(self, entity_type: str, entity_id: str) -> None:
        """Remove an entity from the search index."""
        await self._pool.execute(
            "DELETE FROM dbo.search_index WHERE entity_type = $1 AND entity_id = $2",
            entity_type, entity_id,
        )
