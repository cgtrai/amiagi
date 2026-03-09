"""Activity logger for Web GUI — records user actions to PostgreSQL.

Each action is stored in ``dbo.user_activity_log`` with JSONB detail.
Supports filtering, CSV export, retention-based cleanup, and a background
retention scheduler that runs cleanup daily.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Default retention
DEFAULT_RETENTION_DAYS = 90
_SCHEDULER_INTERVAL_S = 86_400  # 24 hours


class WebActivityLogger:
    """Logs user actions to the ``user_activity_log`` table."""

    def __init__(self, pool: "asyncpg.Pool", retention_days: int | None = DEFAULT_RETENTION_DAYS) -> None:
        self._pool = pool
        self._retention_days = retention_days
        self._scheduler_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def log(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        action: str,
        detail: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> int:
        """Record an activity entry. Returns the auto-generated log entry ID."""
        row_id = await self._pool.fetchval(
            """
            INSERT INTO dbo.user_activity_log (user_id, session_id, action, detail, ip_address, created_at)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::inet, $6)
            RETURNING id
            """,
            user_id,
            session_id,
            action,
            json.dumps(detail or {}),
            ip_address,
            datetime.now(timezone.utc),
        )
        return row_id

    # ------------------------------------------------------------------
    # Read (with filtering)
    # ------------------------------------------------------------------

    async def query(
        self,
        *,
        user_id: str | None = None,
        action: str | None = None,
        action_match: str = "exact",
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
        search: str | None = None,
        error_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query activity log with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1
        if action:
            if action_match == "contains":
                conditions.append(f"LOWER(action) LIKE LOWER(${idx})")
                params.append(f"%{action}%")
            else:
                conditions.append(f"action = ${idx}")
                params.append(action)
            idx += 1
        if since:
            conditions.append(f"created_at >= ${idx}")
            params.append(since)
            idx += 1
        if until:
            conditions.append(f"created_at <= ${idx}")
            params.append(until)
            idx += 1
        if session_id:
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)
            idx += 1
        if search:
            conditions.append(
                "(" 
                f"CAST(user_id AS text) ILIKE ${idx} OR "
                f"CAST(session_id AS text) ILIKE ${idx} OR "
                f"action ILIKE ${idx} OR "
                f"CAST(detail AS text) ILIKE ${idx} OR "
                f"CAST(ip_address AS text) ILIKE ${idx}" 
                ")"
            )
            params.append(f"%{search}%")
            idx += 1
        if error_only:
            conditions.append(
                "(" 
                "LOWER(action) LIKE '%error%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%error%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%fail%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%exception%'"
                ")"
            )

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        rows = await self._pool.fetch(
            f"""
            SELECT id, user_id, session_id, action, detail, ip_address, created_at
            FROM dbo.user_activity_log
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )
        return [dict(r) for r in rows]

    async def count(
        self,
        *,
        user_id: str | None = None,
        action: str | None = None,
        action_match: str = "exact",
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
        search: str | None = None,
        error_only: bool = False,
    ) -> int:
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1
        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1
        if action:
            if action_match == "contains":
                conditions.append(f"LOWER(action) LIKE LOWER(${idx})")
                params.append(f"%{action}%")
            else:
                conditions.append(f"action = ${idx}")
                params.append(action)
            idx += 1
        if since:
            conditions.append(f"created_at >= ${idx}")
            params.append(since)
            idx += 1
        if until:
            conditions.append(f"created_at <= ${idx}")
            params.append(until)
            idx += 1
        if session_id:
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)
            idx += 1
        if search:
            conditions.append(
                "(" 
                f"CAST(user_id AS text) ILIKE ${idx} OR "
                f"CAST(session_id AS text) ILIKE ${idx} OR "
                f"action ILIKE ${idx} OR "
                f"CAST(detail AS text) ILIKE ${idx} OR "
                f"CAST(ip_address AS text) ILIKE ${idx}" 
                ")"
            )
            params.append(f"%{search}%")
            idx += 1
        if error_only:
            conditions.append(
                "(" 
                "LOWER(action) LIKE '%error%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%error%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%fail%' OR "
                "LOWER(CAST(detail AS text)) LIKE '%exception%'"
                ")"
            )
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        return await self._pool.fetchval(
            f"SELECT count(*) FROM dbo.user_activity_log {where}", *params,
        )

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    async def export_csv(self, **filters: Any) -> str:
        """Export filtered log entries as CSV string."""
        rows = await self.query(**filters, limit=10000)
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["id", "user_id", "session_id", "action", "detail", "ip_address", "created_at"],
        )
        writer.writeheader()
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else ""
            r["detail"] = str(r.get("detail", ""))
            writer.writerow(r)
        return buf.getvalue()

    async def export_rows(self, **filters: Any) -> list[dict[str, Any]]:
        """Export filtered log entries as JSON-serialisable list of dicts."""
        rows = await self.query(**filters, limit=10000)
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else ""
            r["detail"] = str(r.get("detail", ""))
            r["id"] = str(r.get("id", ""))
        return rows

    # ------------------------------------------------------------------
    # Retention cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_entries(self) -> int:
        """Delete entries older than retention period. Returns count deleted."""
        if self._retention_days is None:
            logger.info("Audit retention cleanup skipped (retention=forever)")
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        result = await self._pool.execute(
            "DELETE FROM dbo.user_activity_log WHERE created_at < $1", cutoff,
        )
        # result is like 'DELETE 42'
        try:
            count = int(result.split()[-1])
        except (ValueError, IndexError):
            count = 0
        logger.info("Cleaned up %d audit entries older than %d days", count, self._retention_days)
        return count

    # ------------------------------------------------------------------
    # Retention scheduler
    # ------------------------------------------------------------------

    def start_retention_scheduler(self) -> None:
        """Launch a background task that calls ``cleanup_old_entries()`` daily."""
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.ensure_future(self._retention_loop())
            logger.info(
                "Retention scheduler started (interval=%ds, retention=%dd)",
                _SCHEDULER_INTERVAL_S,
                self._retention_days if self._retention_days is not None else -1,
            )

    def stop_retention_scheduler(self) -> None:
        """Cancel the background retention task."""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            logger.info("Retention scheduler stopped")

    async def _retention_loop(self) -> None:
        """Periodically clean up old entries."""
        while True:
            await asyncio.sleep(_SCHEDULER_INTERVAL_S)
            try:
                deleted = await self.cleanup_old_entries()
                logger.info("Scheduled retention cleanup deleted %d entries", deleted)
            except Exception:
                logger.exception("Retention cleanup failed")
