"""Global search — full-text search via PostgreSQL tsvector + GIN.

Searches across agents, tasks, files, prompts, skills, and snippets.
Uses ``dbo.search_index`` with auto-updated tsvector column.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from urllib.parse import quote

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
    url: str | None = None

    def __post_init__(self) -> None:
        if self.url is None:
            self.url = _build_result_url(self.entity_type, self.entity_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "title": self.title,
            "snippet": self.snippet,
            "rank": round(self.rank, 4),
            "url": self.url,
        }


def _build_result_url(entity_type: str, entity_id: str) -> str | None:
    kind = (entity_type or "").lower()
    eid = quote(str(entity_id or ""), safe="")
    builders = {
        "agent": lambda: f"/agents/{eid}",
        "task": lambda: f"/tasks?task_id={eid}",
        "file": lambda: f"/files?path={eid}",
        "skill": lambda: "/admin/skills",
        "prompt": lambda: "/prompt-library",
        "snippet": lambda: "/snippets-library",
        "session": lambda: f"/sessions?session_id={eid}",
        "workflow": lambda: f"/workflows?definition_id={eid}",
        "workflow_run": lambda: f"/workflows?run_id={eid}",
        "template": lambda: "/tasks/new",
        "knowledge": lambda: "/knowledge",
        "inbox": lambda: f"/inbox?item_id={eid}",
        "user": lambda: f"/admin/users/{eid}",
        "role": lambda: "/admin/roles",
        "vault": lambda: "/admin/vault",
    }
    builder = builders.get(kind)
    return builder() if builder is not None else None


class SearchService:
    """Full-text search over the unified search index."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool
        self._recent_queries: list[str] = []
        self._query_stats: dict[str, dict[str, Any]] = {}
        self._query_seq = 0

    async def search(
        self,
        query: str,
        *,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        remember: bool = True,
    ) -> list[SearchResult]:
        """Search the index using PostgreSQL full-text search."""
        if not query or not query.strip():
            return []

        query = query.strip()
        if remember:
            self.remember_query(query)

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

    def remember_query(self, query: str) -> None:
        text = str(query or "").strip()
        if len(text) < 2:
            return
        key = text.casefold()
        self._query_seq += 1
        stats = self._query_stats.get(key, {"query": text, "count": 0, "last_seen": 0})
        stats["query"] = text
        stats["count"] = int(stats.get("count") or 0) + 1
        stats["last_seen"] = self._query_seq
        self._query_stats[key] = stats
        self._recent_queries = [item for item in self._recent_queries if item != text]
        self._recent_queries.insert(0, text)
        if len(self._recent_queries) > 20:
            self._recent_queries = self._recent_queries[:20]

    def get_recent_queries(self, *, limit: int = 5) -> list[str]:
        return list(self._recent_queries[:max(0, limit)])

    def get_frequent_queries(self, partial: str = "", *, limit: int = 5) -> list[str]:
        needle = str(partial or "").strip().casefold()
        items = []
        for stats in self._query_stats.values():
            query = str(stats.get("query") or "").strip()
            if not query:
                continue
            lowered = query.casefold()
            if needle and needle not in lowered:
                continue
            items.append(stats)
        items.sort(
            key=lambda item: (
                0 if needle and str(item.get("query") or "").casefold().startswith(needle) else 1,
                -int(item.get("count") or 0),
                -int(item.get("last_seen") or 0),
                len(str(item.get("query") or "")),
            )
        )
        return [str(item.get("query") or "") for item in items[:max(0, limit)]]

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
