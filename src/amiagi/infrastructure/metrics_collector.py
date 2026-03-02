"""MetricsCollector — in-memory ring buffer with periodic flush to SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MetricPoint:
    """A single metric measurement."""

    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Collects system metrics into a ring buffer and flushes to SQLite.

    Thread-safe.  Typical usage::

        collector = MetricsCollector(db_path=Path("./data/metrics.db"))
        collector.record("task.duration_s", 12.5, tags={"agent": "polluks"})
        recent = collector.query("task.duration_s", last_n=10)
        collector.flush()
    """

    def __init__(
        self,
        *,
        db_path: Path = Path("./data/metrics.db"),
        buffer_size: int = 10_000,
        auto_flush_every: int = 500,
    ) -> None:
        self._db_path = db_path
        self._buffer: deque[MetricPoint] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._auto_flush_every = auto_flush_every
        self._unflushed = 0
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ---- public API ----

    def record(
        self,
        name: str,
        value: float,
        *,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a metric point."""
        point = MetricPoint(
            name=name,
            value=value,
            tags=tags or {},
        )
        with self._lock:
            self._buffer.append(point)
            self._unflushed += 1
            if self._unflushed >= self._auto_flush_every:
                self._flush_locked()

    def query(
        self,
        name: str,
        *,
        last_n: int = 100,
        since: float | None = None,
    ) -> list[MetricPoint]:
        """Query recent metrics from the buffer + DB."""
        with self._lock:
            # First check in-memory buffer
            results = [
                p for p in self._buffer
                if p.name == name
                and (since is None or p.timestamp >= since)
            ]
        # If not enough, query DB
        if len(results) < last_n:
            db_results = self._query_db(name, last_n=last_n, since=since)
            # Merge and deduplicate by timestamp
            seen = {p.timestamp for p in results}
            for p in db_results:
                if p.timestamp not in seen:
                    results.append(p)
                    seen.add(p.timestamp)
        results.sort(key=lambda p: p.timestamp)
        return results[-last_n:]

    def query_all_names(self) -> list[str]:
        """Return distinct metric names from the buffer."""
        with self._lock:
            return sorted({p.name for p in self._buffer})

    def flush(self) -> int:
        """Flush buffered metrics to SQLite. Returns count flushed."""
        with self._lock:
            return self._flush_locked()

    def summary(self) -> dict[str, dict[str, float]]:
        """Return per-metric name stats: count, sum, min, max, avg."""
        with self._lock:
            stats: dict[str, dict[str, float]] = {}
            for point in self._buffer:
                if point.name not in stats:
                    stats[point.name] = {
                        "count": 0, "sum": 0.0,
                        "min": float("inf"), "max": float("-inf"),
                    }
                s = stats[point.name]
                s["count"] += 1
                s["sum"] += point.value
                s["min"] = min(s["min"], point.value)
                s["max"] = max(s["max"], point.value)
            for s in stats.values():
                s["avg"] = s["sum"] / max(s["count"], 1)
            return stats

    @property
    def buffer_size(self) -> int:
        with self._lock:
            return len(self._buffer)

    # ---- internals ----

    def _init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    tags TEXT NOT NULL DEFAULT '{}',
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(name, timestamp)"
            )

    def _flush_locked(self) -> int:
        """Must be called with self._lock held."""
        if not self._buffer:
            return 0
        points = list(self._buffer)
        self._buffer.clear()
        self._unflushed = 0

        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.executemany(
                    "INSERT INTO metrics (name, value, tags, timestamp) VALUES (?, ?, ?, ?)",
                    [
                        (p.name, p.value, json.dumps(p.tags, ensure_ascii=False), p.timestamp)
                        for p in points
                    ],
                )
            return len(points)
        except Exception:
            # Put points back on failure
            for p in points:
                self._buffer.append(p)
            return 0

    def _query_db(
        self,
        name: str,
        *,
        last_n: int = 100,
        since: float | None = None,
    ) -> list[MetricPoint]:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                if since is not None:
                    rows = conn.execute(
                        "SELECT name, value, tags, timestamp FROM metrics "
                        "WHERE name = ? AND timestamp >= ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (name, since, last_n),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT name, value, tags, timestamp FROM metrics "
                        "WHERE name = ? ORDER BY timestamp DESC LIMIT ?",
                        (name, last_n),
                    ).fetchall()
            return [
                MetricPoint(
                    name=row[0],
                    value=row[1],
                    tags=json.loads(row[2]) if row[2] else {},
                    timestamp=row[3],
                )
                for row in rows
            ]
        except Exception:
            return []
