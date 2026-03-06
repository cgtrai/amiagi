"""SandboxMonitor — resource tracking, alerts & DB persistence for sandboxes.

Provides:
- Periodic resource measurement (size, file count) per sandbox
- DB-backed execution log (`shell_executions` table)
- Cleanup hooks for temp files
- Alert callbacks on size limit breaches
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from amiagi.infrastructure.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────

@dataclass(frozen=True)
class SandboxSnapshot:
    """Point-in-time snapshot of a sandbox's resource usage."""
    agent_id: str
    path: str
    size_bytes: int
    file_count: int
    max_size_bytes: int
    utilization_pct: float
    measured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ShellExecution:
    """Record of a shell command execution attempt."""
    id: str
    agent_id: str
    command: str
    exit_code: int | None
    blocked: bool
    block_reason: str | None
    duration_ms: int | None
    stdout_preview: str | None
    stderr_preview: str | None
    sandbox_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "command": self.command,
            "exit_code": self.exit_code,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "duration_ms": self.duration_ms,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "sandbox_id": self.sandbox_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── SandboxMonitor ────────────────────────────────────────────

class SandboxMonitor:
    """Monitors sandbox resource usage and logs shell executions.

    Parameters
    ----------
    sandbox_manager:
        The ``SandboxManager`` providing sandbox CRUD.
    db_pool:
        Database connection pool (asyncpg or SQLite wrapper).
    default_max_size:
        Default per-sandbox size limit in bytes (256 MB).
    scan_interval:
        Seconds between automatic resource scans (0 = disabled).
    on_limit_breach:
        Optional async callback ``(snapshot) -> None`` invoked when
        a sandbox exceeds its size limit.
    """

    def __init__(
        self,
        sandbox_manager: "SandboxManager",
        db_pool: Any = None,
        *,
        default_max_size: int = 268_435_456,  # 256 MB
        scan_interval: float = 0,
        on_limit_breach: Callable[..., Any] | None = None,
    ) -> None:
        self._mgr = sandbox_manager
        self._pool = db_pool
        self._default_max = default_max_size
        self._scan_interval = scan_interval
        self._on_breach = on_limit_breach
        self._scan_task: asyncio.Task | None = None
        self._snapshots: dict[str, SandboxSnapshot] = {}

    # ── Resource scanning ─────────────────────────────────────

    def scan(self, agent_id: str) -> SandboxSnapshot:
        """Measure a single sandbox synchronously."""
        path = self._mgr.get(agent_id)
        if path is None:
            return SandboxSnapshot(
                agent_id=agent_id, path="", size_bytes=0,
                file_count=0, max_size_bytes=self._default_max,
                utilization_pct=0.0,
            )

        size = self._mgr.sandbox_size(agent_id)
        file_count = sum(
            1 for _, _, files in os.walk(path) for _ in files
        )
        max_sz = self._default_max
        util = round(size / max_sz * 100, 1) if max_sz > 0 else 0

        snap = SandboxSnapshot(
            agent_id=agent_id,
            path=str(path),
            size_bytes=size,
            file_count=file_count,
            max_size_bytes=max_sz,
            utilization_pct=util,
        )
        self._snapshots[agent_id] = snap
        return snap

    def scan_all(self) -> list[SandboxSnapshot]:
        """Scan all registered sandboxes."""
        results = []
        for aid in list(self._mgr.list_sandboxes()):
            results.append(self.scan(aid))
        return results

    @property
    def snapshots(self) -> dict[str, SandboxSnapshot]:
        return dict(self._snapshots)

    # ── Periodic auto-scan ────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start periodic resource scanning (if scan_interval > 0)."""
        if self._scan_interval <= 0:
            return
        lp = loop or asyncio.get_event_loop()
        self._scan_task = lp.create_task(self._scan_loop())

    async def _scan_loop(self) -> None:
        while True:
            try:
                snaps = self.scan_all()
                for s in snaps:
                    if s.utilization_pct >= 100 and self._on_breach:
                        try:
                            result = self._on_breach(s)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.debug("Breach callback failed for %s", s.agent_id, exc_info=True)
                    # Update DB metadata
                    await self._update_sandbox_meta(s)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("SandboxMonitor scan failed", exc_info=True)
            await asyncio.sleep(self._scan_interval)

    def stop(self) -> None:
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            self._scan_task = None

    # ── Cleanup helpers ───────────────────────────────────────

    def cleanup_tmp(self, agent_id: str) -> int:
        """Remove temporary files from sandbox. Returns bytes freed."""
        path = self._mgr.get(agent_id)
        if path is None:
            return 0
        freed = 0
        tmp_patterns = ["*.tmp", "*.pyc", "__pycache__"]
        for pattern in tmp_patterns:
            for f in path.rglob(pattern):
                try:
                    sz = f.stat().st_size if f.is_file() else 0
                    if f.is_file():
                        f.unlink()
                        freed += sz
                    elif f.is_dir():
                        import shutil
                        for child in f.rglob("*"):
                            if child.is_file():
                                freed += child.stat().st_size
                        shutil.rmtree(f, ignore_errors=True)
                except Exception:
                    pass
        return freed

    def reset(self, agent_id: str) -> bool:
        """Reset sandbox to clean state (destroy + recreate)."""
        path = self._mgr.get(agent_id)
        if path is None:
            return False
        self._mgr.destroy(agent_id)
        self._mgr.create(agent_id)
        self._snapshots.pop(agent_id, None)
        return True

    # ── Shell execution logging ───────────────────────────────

    async def log_execution(
        self,
        *,
        agent_id: str,
        command: str,
        exit_code: int | None = None,
        blocked: bool = False,
        block_reason: str | None = None,
        duration_ms: int | None = None,
        stdout_preview: str | None = None,
        stderr_preview: str | None = None,
        sandbox_id: str | None = None,
    ) -> ShellExecution:
        """Record a shell execution to the database."""
        exec_id = str(uuid.uuid4())
        entry = ShellExecution(
            id=exec_id,
            agent_id=agent_id,
            command=command,
            exit_code=exit_code,
            blocked=blocked,
            block_reason=block_reason,
            duration_ms=duration_ms,
            stdout_preview=(stdout_preview or "")[:2000],
            stderr_preview=(stderr_preview or "")[:2000],
            sandbox_id=sandbox_id,
        )

        if self._pool is not None:
            try:
                await self._db_insert_execution(entry)
            except Exception:
                logger.debug("Failed to log execution to DB", exc_info=True)

        return entry

    async def list_executions(
        self,
        *,
        agent_id: str | None = None,
        blocked_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve recent shell executions from DB."""
        if self._pool is None:
            return []
        try:
            return await self._db_list_executions(
                agent_id=agent_id, blocked_only=blocked_only, limit=limit
            )
        except Exception:
            logger.debug("Failed to list executions", exc_info=True)
            return []

    # ── DB helpers ────────────────────────────────────────────

    async def _db_insert_execution(self, e: ShellExecution) -> None:
        pool = self._pool
        if hasattr(pool, "acquire"):  # asyncpg
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO dbo.shell_executions
                       (id, agent_id, command, exit_code, blocked, block_reason,
                        duration_ms, stdout_preview, stderr_preview, sandbox_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    uuid.UUID(e.id), e.agent_id, e.command, e.exit_code,
                    e.blocked, e.block_reason, e.duration_ms,
                    e.stdout_preview, e.stderr_preview, e.sandbox_id,
                )
        else:  # SQLite
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO shell_executions
                       (id, agent_id, command, exit_code, blocked, block_reason,
                        duration_ms, stdout_preview, stderr_preview, sandbox_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (e.id, e.agent_id, e.command, e.exit_code,
                     int(e.blocked), e.block_reason, e.duration_ms,
                     e.stdout_preview, e.stderr_preview, e.sandbox_id),
                )
                await conn.commit()

    async def _db_list_executions(
        self, *, agent_id: str | None, blocked_only: bool, limit: int,
    ) -> list[dict[str, Any]]:
        pool = self._pool
        rows: list[Any] = []
        if hasattr(pool, "acquire") and hasattr(pool, "get_size"):  # asyncpg
            async with pool.acquire() as conn:
                clauses = []
                params: list[Any] = []
                idx = 1
                if agent_id:
                    clauses.append(f"agent_id = ${idx}")
                    params.append(agent_id)
                    idx += 1
                if blocked_only:
                    clauses.append("blocked = true")
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                params.append(limit)
                rows = await conn.fetch(
                    f"SELECT * FROM dbo.shell_executions {where} "
                    f"ORDER BY created_at DESC LIMIT ${idx}",
                    *params,
                )
            return [dict(r) for r in rows]
        else:  # SQLite
            async with pool.acquire() as conn:
                clauses = []
                params_list: list[Any] = []
                if agent_id:
                    clauses.append("agent_id = ?")
                    params_list.append(agent_id)
                if blocked_only:
                    clauses.append("blocked = 1")
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                params_list.append(limit)
                cursor = await conn.execute(
                    f"SELECT * FROM shell_executions {where} "
                    f"ORDER BY created_at DESC LIMIT ?",
                    tuple(params_list),
                )
                rows = await cursor.fetchall()
                if rows and hasattr(cursor, "description") and cursor.description:
                    cols = [d[0] for d in cursor.description]
                    return [dict(zip(cols, r)) for r in rows]
                return []

    async def _update_sandbox_meta(self, snap: SandboxSnapshot) -> None:
        if self._pool is None:
            return
        try:
            pool = self._pool
            if hasattr(pool, "acquire") and hasattr(pool, "get_size"):  # asyncpg
                async with pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO dbo.sandbox_metadata
                           (agent_id, sandbox_path, size_bytes, file_count,
                            max_size_bytes, last_accessed)
                           VALUES ($1,$2,$3,$4,$5, now())
                           ON CONFLICT (agent_id)
                           DO UPDATE SET size_bytes=$3, file_count=$4,
                                         last_accessed=now()""",
                        snap.agent_id, snap.path, snap.size_bytes,
                        snap.file_count, snap.max_size_bytes,
                    )
            else:  # SQLite
                async with pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO sandbox_metadata
                           (agent_id, sandbox_path, size_bytes, file_count,
                            max_size_bytes, last_accessed)
                           VALUES (?,?,?,?,?, CURRENT_TIMESTAMP)
                           ON CONFLICT (agent_id)
                           DO UPDATE SET size_bytes=excluded.size_bytes,
                                         file_count=excluded.file_count,
                                         last_accessed=CURRENT_TIMESTAMP""",
                        (snap.agent_id, snap.path, snap.size_bytes,
                         snap.file_count, snap.max_size_bytes),
                    )
                    await conn.commit()
        except Exception:
            logger.debug("Failed to update sandbox metadata", exc_info=True)
