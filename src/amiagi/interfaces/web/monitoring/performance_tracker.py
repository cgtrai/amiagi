"""Performance tracker — records agent task performance metrics.

Writes to ``dbo.agent_performance``. Designed to be hooked into
EventBus CycleFinishedEvent for automatic recording.
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
class PerformanceRecord:
    id: int
    agent_role: str
    model: str | None
    task_type: str | None
    duration_ms: int | None
    success: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_role": self.agent_role,
            "model": self.model,
            "task_type": self.task_type,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": float(self.cost_usd),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_record(row) -> PerformanceRecord:
    return PerformanceRecord(
        id=row["id"],
        agent_role=row["agent_role"],
        model=row.get("model"),
        task_type=row.get("task_type"),
        duration_ms=row.get("duration_ms"),
        success=row["success"],
        tokens_in=row.get("tokens_in", 0),
        tokens_out=row.get("tokens_out", 0),
        cost_usd=float(row.get("cost_usd", 0)),
        created_at=row.get("created_at"),
    )


class PerformanceTracker:
    """Records and queries agent performance metrics."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def record(
        self,
        agent_role: str,
        *,
        model: str | None = None,
        task_type: str | None = None,
        duration_ms: int | None = None,
        success: bool = True,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        """Insert a performance record. Returns the row id."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.agent_performance
                (agent_role, model, task_type, duration_ms, success, tokens_in, tokens_out, cost_usd)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            agent_role, model, task_type, duration_ms, success, tokens_in, tokens_out, cost_usd,
        )
        return row["id"]

    async def query(
        self,
        *,
        agent_role: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[PerformanceRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1
        if agent_role:
            conditions.append(f"agent_role = ${idx}")
            params.append(agent_role)
            idx += 1
        if model:
            conditions.append(f"model = ${idx}")
            params.append(model)
            idx += 1
        if since:
            conditions.append(f"created_at >= ${idx}::timestamptz")
            params.append(since)
            idx += 1
        if until:
            conditions.append(f"created_at <= ${idx}::timestamptz")
            params.append(until)
            idx += 1
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        sql = f"""
            SELECT * FROM dbo.agent_performance {where}
            ORDER BY created_at DESC LIMIT ${idx}
        """
        rows = await self._pool.fetch(sql, *params)
        return [_row_to_record(r) for r in rows]

    async def summary(
        self, agent_role: str | None = None, model: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate stats: avg duration, success rate, total tokens."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1
        if agent_role:
            conditions.append(f"agent_role = ${idx}")
            params.append(agent_role)
            idx += 1
        if model:
            conditions.append(f"model = ${idx}")
            params.append(model)
            idx += 1
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT
                count(*) AS total,
                avg(duration_ms) AS avg_duration_ms,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                sum(CASE WHEN success THEN 1 ELSE 0 END)::float / NULLIF(count(*), 0) AS success_rate,
                sum(tokens_in) AS total_tokens_in,
                sum(tokens_out) AS total_tokens_out,
                sum(cost_usd) AS total_cost_usd
            FROM dbo.agent_performance {where}
        """
        row = await self._pool.fetchrow(sql, *params)
        if not row:
            return {"total": 0}
        return {
            "total": row["total"],
            "avg_duration_ms": float(row["avg_duration_ms"]) if row["avg_duration_ms"] else None,
            "p50_ms": float(row["p50_ms"]) if row["p50_ms"] else None,
            "p95_ms": float(row["p95_ms"]) if row["p95_ms"] else None,
            "success_rate": float(row["success_rate"]) if row["success_rate"] else None,
            "total_tokens_in": row["total_tokens_in"] or 0,
            "total_tokens_out": row["total_tokens_out"] or 0,
            "total_cost_usd": float(row["total_cost_usd"]) if row["total_cost_usd"] else 0,
        }
