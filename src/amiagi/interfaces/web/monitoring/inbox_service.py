"""Inbox service — Human-in-the-Loop aggregator.

Manages ``dbo.inbox_items`` that represent pending human decisions:
gate approvals, agent questions, review requests, etc.

Each item has a *source_type* (``workflow``, ``agent``, ``tool``) and
a *status* (``pending``, ``approved``, ``rejected``, ``expired``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


# ── Data model ───────────────────────────────────────────────


@dataclass
class InboxItem:
    id: str
    item_type: str
    title: str
    body: str
    source_type: str
    source_id: str | None
    node_id: str | None
    agent_id: str | None
    status: str
    priority: int
    resolution: str | None
    resolved_by: str | None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item_type": self.item_type,
            "title": self.title,
            "body": self.body,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "priority": self.priority,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "metadata": self.metadata,
        }


def _row_to_item(row) -> InboxItem:
    import json

    meta = row.get("metadata") or "{}"
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return InboxItem(
        id=str(row["id"]),
        item_type=row["item_type"],
        title=row["title"],
        body=row.get("body", ""),
        source_type=row["source_type"],
        source_id=row.get("source_id"),
        node_id=row.get("node_id"),
        agent_id=row.get("agent_id"),
        status=row["status"],
        priority=row.get("priority", 0),
        resolution=row.get("resolution"),
        resolved_by=row.get("resolved_by"),
        created_at=row.get("created_at"),
        resolved_at=row.get("resolved_at"),
        metadata=meta,
    )


# ── Service ──────────────────────────────────────────────────


class InboxService:
    """CRUD for inbox_items + aggregation helpers."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ── Create ───────────────────────────────────────────────

    async def create(
        self,
        *,
        item_type: str = "gate_approval",
        title: str,
        body: str = "",
        source_type: str = "workflow",
        source_id: str | None = None,
        node_id: str | None = None,
        agent_id: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> InboxItem:
        import json

        meta_json = json.dumps(metadata or {})
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.inbox_items
                (item_type, title, body, source_type, source_id,
                 node_id, agent_id, priority, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            item_type, title, body, source_type, source_id,
            node_id, agent_id, priority, meta_json,
        )
        item = _row_to_item(row)
        logger.info("Inbox item created: %s [%s] — %s", item.id, item.item_type, item.title)
        return item

    # ── Read ─────────────────────────────────────────────────

    async def list_items(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InboxItem]:
        if status:
            rows = await self._pool.fetch(
                "SELECT * FROM dbo.inbox_items WHERE status = $1 "
                "ORDER BY priority DESC, created_at DESC LIMIT $2 OFFSET $3",
                status, limit, offset,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT * FROM dbo.inbox_items "
                "ORDER BY priority DESC, created_at DESC LIMIT $1 OFFSET $2",
                limit, offset,
            )
        return [_row_to_item(r) for r in rows]

    async def get(self, item_id: str) -> InboxItem | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.inbox_items WHERE id = $1", item_id,
        )
        return _row_to_item(row) if row else None

    async def pending_count(self) -> int:
        row = await self._pool.fetchrow(
            "SELECT count(*) AS cnt FROM dbo.inbox_items WHERE status = 'pending'",
        )
        return row["cnt"] if row else 0

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._pool.fetch(
            "SELECT status, count(*) AS cnt FROM dbo.inbox_items GROUP BY status",
        )
        return {r["status"]: r["cnt"] for r in rows}

    # ── Resolve ──────────────────────────────────────────────

    async def approve(
        self, item_id: str, *, resolved_by: str = "operator",
    ) -> InboxItem | None:
        return await self._resolve(item_id, "approved", resolved_by)

    async def reject(
        self, item_id: str, *, resolved_by: str = "operator", reason: str = "",
    ) -> InboxItem | None:
        return await self._resolve(item_id, "rejected", resolved_by, reason)

    async def _resolve(
        self,
        item_id: str,
        resolution: str,
        resolved_by: str,
        reason: str = "",
    ) -> InboxItem | None:
        row = await self._pool.fetchrow(
            """
            UPDATE dbo.inbox_items
            SET status = $2, resolution = $3, resolved_by = $4,
                resolved_at = now()
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            item_id, resolution, reason or resolution, resolved_by,
        )
        if row is None:
            return None
        item = _row_to_item(row)
        logger.info("Inbox item %s resolved: %s by %s", item_id, resolution, resolved_by)
        return item

    # ── Cleanup ──────────────────────────────────────────────

    async def expire_old(self, hours: int = 72) -> int:
        """Mark pending items older than *hours* as expired."""
        result = await self._pool.execute(
            """
            UPDATE dbo.inbox_items
            SET status = 'expired', resolution = 'auto-expired',
                resolved_at = now()
            WHERE status = 'pending'
              AND created_at < now() - INTERVAL '1 hour' * $1
            """,
            hours,
        )
        # Extract count from "UPDATE N"
        try:
            return int(result.split()[-1])
        except (IndexError, ValueError):
            return 0
