"""CronScheduler — lightweight recurring-task scheduler.

Uses asyncio-based scheduling with cron expressions (parsed via a minimal
built-in parser that supports ``m h dom mon dow`` format).  Jobs are
persisted in PostgreSQL via the ``dbo.cron_jobs`` table.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ── Cron expression helpers ──────────────────────────────────────

_FIELD_NAMES = ("minute", "hour", "day", "month", "weekday")


def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field into a set of valid integer values."""
    values: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if part == "*":
            values.update(range(lo, hi + 1))
        elif "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            start = lo if base == "*" else int(base)
            values.update(range(start, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        else:
            values.add(int(part))
    return {v for v in values if lo <= v <= hi}


def parse_cron(expr: str) -> dict[str, set[int]]:
    """Parse a 5-field cron expression into a dict of allowed values."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(parts)}: {expr!r}")

    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    result: dict[str, set[int]] = {}
    for name, field_str, (lo, hi) in zip(_FIELD_NAMES, parts, ranges):
        result[name] = _parse_field(field_str, lo, hi)
    return result


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return ``True`` if *dt* matches the cron expression."""
    fields = parse_cron(expr)
    return (
        dt.minute in fields["minute"]
        and dt.hour in fields["hour"]
        and dt.day in fields["day"]
        and dt.month in fields["month"]
        and dt.weekday() in fields["weekday"]  # Monday=0 … Sunday=6
    )


# ── Data model ───────────────────────────────────────────────────

@dataclass
class CronJob:
    """A persisted recurring-task definition."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    cron_expr: str = "* * * * *"
    task_title: str = ""
    task_description: str = ""
    enabled: bool = True
    last_run: str | None = None  # ISO-8601
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cron_expr": self.cron_expr,
            "task_title": self.task_title,
            "task_description": self.task_description,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "created_at": self.created_at,
        }


# ── Scheduler ────────────────────────────────────────────────────

class CronScheduler:
    """Manages cron jobs — persistence via asyncpg pool, tick every 60 s."""

    def __init__(
        self,
        pool: Any,
        *,
        schema: str = "dbo",
        on_fire: Callable[[CronJob], Awaitable[None]] | None = None,
    ) -> None:
        self._pool = pool
        self._schema = schema
        self._on_fire = on_fire
        self._task: asyncio.Task | None = None
        self._jobs: list[CronJob] = []

    # ── lifecycle ──

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        """Wake up every 60 s and fire matching jobs."""
        try:
            while True:
                await asyncio.sleep(60)
                now = datetime.utcnow()
                for job in list(self._jobs):
                    if not job.enabled:
                        continue
                    try:
                        if cron_matches(job.cron_expr, now):
                            logger.info("Cron firing: %s (%s)", job.name, job.cron_expr)
                            job.last_run = now.isoformat()
                            await self._update_last_run(job)
                            if self._on_fire:
                                await self._on_fire(job)
                    except Exception:
                        logger.exception("Cron tick error for job %s", job.id)
        except asyncio.CancelledError:
            pass

    # ── CRUD ──

    async def load_jobs(self) -> list[CronJob]:
        """Load all jobs from DB into memory."""
        rows = await self._pool.fetch(
            f"SELECT id, name, cron_expr, task_title, task_description, "
            f"enabled, last_run, created_at FROM {self._schema}.cron_jobs ORDER BY created_at"
        )
        self._jobs = [
            CronJob(
                id=r["id"],
                name=r["name"],
                cron_expr=r["cron_expr"],
                task_title=r["task_title"],
                task_description=r["task_description"] or "",
                enabled=r["enabled"],
                last_run=r["last_run"].isoformat() if r["last_run"] else None,
                created_at=r["created_at"].isoformat() if r["created_at"] else "",
            )
            for r in rows
        ]
        return self._jobs

    async def create_job(self, job: CronJob) -> CronJob:
        # Validate cron expression
        parse_cron(job.cron_expr)
        await self._pool.execute(
            f"INSERT INTO {self._schema}.cron_jobs "
            f"(id, name, cron_expr, task_title, task_description, enabled) "
            f"VALUES ($1, $2, $3, $4, $5, $6)",
            job.id, job.name, job.cron_expr, job.task_title, job.task_description, job.enabled,
        )
        self._jobs.append(job)
        return job

    async def delete_job(self, job_id: str) -> bool:
        tag = await self._pool.execute(
            f"DELETE FROM {self._schema}.cron_jobs WHERE id = $1", job_id,
        )
        self._jobs = [j for j in self._jobs if j.id != job_id]
        return "DELETE 1" in tag

    async def toggle_job(self, job_id: str, enabled: bool) -> bool:
        tag = await self._pool.execute(
            f"UPDATE {self._schema}.cron_jobs SET enabled = $2 WHERE id = $1",
            job_id, enabled,
        )
        for j in self._jobs:
            if j.id == job_id:
                j.enabled = enabled
        return "UPDATE 1" in tag

    async def _update_last_run(self, job: CronJob) -> None:
        await self._pool.execute(
            f"UPDATE {self._schema}.cron_jobs SET last_run = NOW() WHERE id = $1",
            job.id,
        )

    def list_jobs(self) -> list[CronJob]:
        return list(self._jobs)
