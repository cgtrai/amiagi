"""CronScheduler — lightweight recurring-task scheduler.

Uses asyncio-based scheduling with cron expressions (parsed via a minimal
built-in parser that supports ``m h dom mon dow`` format).  Jobs are
persisted in PostgreSQL via the ``dbo.cron_jobs`` table.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ── Cron expression helpers ──────────────────────────────────────

_FIELD_NAMES = ("minute", "hour", "day", "month", "weekday")
_WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


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


def next_cron_trigger(expr: str, *, after: datetime | None = None) -> datetime:
    """Return the next datetime matching *expr* after *after*.

    Searches minute-by-minute up to 366 days ahead.
    """
    parse_cron(expr)
    probe = (after or datetime.utcnow()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = probe + timedelta(days=366)
    while probe <= deadline:
        if cron_matches(expr, probe):
            return probe
        probe += timedelta(minutes=1)
    raise ValueError(f"No matching trigger found for expression: {expr!r}")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _parse_time_value(value: str | None, *, default_hour: int = 9, default_minute: int = 0) -> tuple[int, int]:
    raw = str(value or "").strip()
    if not raw:
        return default_hour, default_minute
    if ":" not in raw:
        raise ValueError(f"Invalid time value: {value!r}")
    hour_str, minute_str = raw.split(":", 1)
    return _clamp(int(hour_str), 0, 23), _clamp(int(minute_str), 0, 59)


def build_cron_expression(schedule: dict[str, Any]) -> str:
    """Build a 5-field cron expression from a reminder-like schedule payload."""
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")

    mode = str(schedule.get("mode") or "custom").strip().lower()
    if mode == "custom":
        expr = str(schedule.get("cron_expr") or schedule.get("cron_expression") or "").strip()
        if not expr:
            raise ValueError("cron expression is required for custom mode")
        parse_cron(expr)
        return expr

    if mode == "hourly":
        minute = _clamp(int(schedule.get("minute", 0)), 0, 59)
        interval_hours = _clamp(int(schedule.get("interval_hours", 1)), 1, 23)
        hour_field = "*" if interval_hours == 1 else f"*/{interval_hours}"
        expr = f"{minute} {hour_field} * * *"
        parse_cron(expr)
        return expr

    hour, minute = _parse_time_value(schedule.get("time"))

    if mode == "daily":
        expr = f"{minute} {hour} * * *"
    elif mode == "weekdays":
        expr = f"{minute} {hour} * * 0,1,2,3,4"
    elif mode == "weekly":
        weekdays_raw = schedule.get("weekdays") or []
        weekdays = sorted({
            _clamp(int(day), 0, 6)
            for day in weekdays_raw
        })
        if not weekdays:
            weekdays = [0]
        expr = f"{minute} {hour} * * {','.join(str(day) for day in weekdays)}"
    elif mode == "monthly":
        day_of_month = _clamp(int(schedule.get("day_of_month", 1)), 1, 31)
        expr = f"{minute} {hour} {day_of_month} * *"
    else:
        raise ValueError(f"Unsupported schedule mode: {mode!r}")

    parse_cron(expr)
    return expr


def cron_to_human(expr: str) -> str:
    """Convert common cron expressions into reminder-like human descriptions."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return expr
    minute, hour, day, month, weekday = parts

    if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
        return f"Every {minute[2:]} minutes"
    if hour.startswith("*/") and day == "*" and month == "*" and weekday == "*":
        return f"Every {hour[2:]} hours at :{int(minute):02d}"
    if hour == "*" and day == "*" and month == "*" and weekday == "*":
        return f"Every hour at :{int(minute):02d}"
    if day == "*" and month == "*" and weekday == "*":
        return f"Every day at {int(hour):02d}:{int(minute):02d}"
    if day == "*" and month == "*" and weekday == "0,1,2,3,4":
        return f"Every weekday at {int(hour):02d}:{int(minute):02d}"
    if day == "*" and month == "*" and weekday != "*":
        try:
            weekdays = [
                _WEEKDAY_NAMES[_clamp(int(token), 0, 6)]
                for token in weekday.split(",")
            ]
            return f"Every {', '.join(weekdays)} at {int(hour):02d}:{int(minute):02d}"
        except ValueError:
            return expr
    if month == "*" and weekday == "*" and day != "*":
        return f"Day {int(day)} of every month at {int(hour):02d}:{int(minute):02d}"
    return expr


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
    next_run: str | None = None

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
            "next_run": self.next_run,
        }


@dataclass(frozen=True)
class CronExecutionRecord:
    """Single cron execution attempt for history UI."""

    job_id: str
    job_name: str
    triggered_at: str
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "triggered_at": self.triggered_at,
            "status": self.status,
            "message": self.message,
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
        self._history: list[CronExecutionRecord] = []

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
                            self._record_history(job, status="success")
                            self._refresh_job_schedule(job)
                    except Exception:
                        self._record_history(job, status="error", message="Cron tick error")
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
        for job in self._jobs:
            self._refresh_job_schedule(job)
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
        self._refresh_job_schedule(job)
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
                self._refresh_job_schedule(j)
        return "UPDATE 1" in tag

    async def _update_last_run(self, job: CronJob) -> None:
        await self._pool.execute(
            f"UPDATE {self._schema}.cron_jobs SET last_run = NOW() WHERE id = $1",
            job.id,
        )

    def list_jobs(self) -> list[CronJob]:
        for job in self._jobs:
            self._refresh_job_schedule(job)
        return list(self._jobs)

    def list_history(self, *, job_id: str | None = None, limit: int = 100) -> list[CronExecutionRecord]:
        records = self._history
        if job_id:
            records = [record for record in records if record.job_id == job_id]
        return records[-limit:][::-1]

    def _refresh_job_schedule(self, job: CronJob) -> None:
        if not job.enabled:
            job.next_run = None
            return
        base = None
        if job.last_run:
            try:
                base = datetime.fromisoformat(job.last_run)
            except ValueError:
                base = None
        try:
            job.next_run = next_cron_trigger(job.cron_expr, after=base).isoformat()
        except ValueError:
            job.next_run = None

    def _record_history(self, job: CronJob, *, status: str, message: str = "") -> None:
        self._history.append(CronExecutionRecord(
            job_id=job.id,
            job_name=job.name,
            triggered_at=datetime.utcnow().isoformat(),
            status=status,
            message=message,
        ))
        if len(self._history) > 200:
            self._history = self._history[-200:]
