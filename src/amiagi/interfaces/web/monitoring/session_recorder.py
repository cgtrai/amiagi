"""Session replay — record and replay session events.

Writes EventBus events to ``dbo.session_events`` for later timeline replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    id: int
    session_id: str
    event_type: str
    agent_id: str | None
    payload: dict[str, Any]
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_event(row) -> SessionEvent:
    payload = row.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    return SessionEvent(
        id=row["id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        agent_id=row.get("agent_id"),
        payload=payload or {},
        created_at=row.get("created_at"),
    )


class SessionRecorder:
    """Records events for session replay."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def record_event(
        self,
        session_id: str,
        event_type: str,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.session_events (session_id, event_type, agent_id, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            session_id, event_type, agent_id, json.dumps(payload or {}),
        )
        return row["id"]

    async def add_events(
        self,
        events: list[dict[str, Any]],
    ) -> int:
        """Batch-insert multiple events in a single round-trip.

        Each dict must contain ``session_id``, ``event_type``; optional
        ``agent_id`` and ``payload``.  Returns the number of rows inserted.
        """
        if not events:
            return 0
        rows = [
            (
                ev["session_id"],
                ev["event_type"],
                ev.get("agent_id"),
                json.dumps(ev.get("payload") or {}),
            )
            for ev in events
        ]
        result = await self._pool.executemany(
            """
            INSERT INTO dbo.session_events (session_id, event_type, agent_id, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            rows,
        )
        return len(rows)

    async def get_session_events(
        self, session_id: str, *, limit: int = 500,
    ) -> list[SessionEvent]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM dbo.session_events
            WHERE session_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            session_id, limit,
        )
        return [_row_to_event(r) for r in rows]

    async def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List distinct sessions with event counts."""
        rows = await self._pool.fetch(
            """
            SELECT session_id, count(*) AS event_count,
                   min(created_at) AS started_at, max(created_at) AS ended_at
            FROM dbo.session_events
            GROUP BY session_id
            ORDER BY max(created_at) DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            {
                "session_id": r["session_id"],
                "event_count": r["event_count"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            }
            for r in rows
        ]


# ------------------------------------------------------------------
# SessionEventBuffer — periodic auto-flush from EventBus
# ------------------------------------------------------------------

class SessionEventBuffer:
    """In-memory buffer that collects EventBus events and bulk-flushes
    them to the database via :class:`SessionRecorder` at a fixed interval.

    Usage in ``app.py`` on_startup::

        buf = SessionEventBuffer(recorder, session_id="current")
        buf.start(loop)          # spawns asyncio flush task
        event_bus.on(LogEvent, buf.on_event)  # wire
        # ... on shutdown:
        await buf.stop()         # final flush + cancel task
    """

    def __init__(
        self,
        recorder: SessionRecorder,
        *,
        session_id: str = "web",
        flush_interval: float = 5.0,
    ) -> None:
        self._recorder = recorder
        self._session_id = session_id
        self._interval = flush_interval
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- public API ---------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Callback compatible with ``event_bus.on(EventType, buf.on_event)``.

        Extracts event_type, agent_id and a lightweight payload, then
        appends to the buffer.  Thread-safe: schedules the append on
        the event loop.
        """
        entry = {
            "session_id": self._session_id,
            "event_type": type(event).__name__,
            "agent_id": getattr(event, "agent_id", None) or getattr(event, "actor", None),
            "payload": {
                k: str(v)[:500] for k, v in vars(event).items()
                if k not in ("_", "__dict__", "__weakref__")
            } if hasattr(event, "__dict__") else {},
        }
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._buffer.append, entry)
        else:
            self._buffer.append(entry)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the periodic flush task."""
        self._loop = loop
        self._task = loop.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Cancel the flush task and perform a final flush."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._flush()

    # -- internals ----------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()
        try:
            await self._recorder.add_events(batch)
        except Exception:
            logger.debug("SessionEventBuffer flush failed", exc_info=True)
