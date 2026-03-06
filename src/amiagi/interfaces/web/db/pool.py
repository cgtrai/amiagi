"""Async database pool management for the web interface.

Supports two back-ends:

* **PostgreSQL** (asyncpg) — used when ``AMIAGI_DB_USER`` is configured.
* **SQLite** (aiosqlite) — automatic fallback for local / single-user mode.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    import asyncpg

    from amiagi.config import Settings
    from amiagi.interfaces.web.db.sqlite_pool import SqlitePool

logger = logging.getLogger(__name__)

_PG_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SQLITE_MIGRATIONS_DIR = Path(__file__).parent / "migrations_sqlite"

# Union type used throughout web interface for pool references.
DbPool = Union["asyncpg.Pool", "SqlitePool"]


def _use_sqlite(settings: "Settings") -> bool:
    """Return ``True`` when PostgreSQL credentials are not configured."""
    return not getattr(settings, "db_user", None)


async def create_pool(settings: "Settings") -> Any:
    """Create and return a database pool.

    Auto-detects the back-end:
    * If ``db_user`` is set → asyncpg (PostgreSQL).
    * Otherwise → SqlitePool (SQLite, stored in ``db_sqlite_path``).
    """
    if _use_sqlite(settings):
        return await _create_sqlite_pool(settings)
    return await _create_pg_pool(settings)


# ── PostgreSQL ───────────────────────────────────────────────

async def _create_pg_pool(settings: "Settings") -> "asyncpg.Pool":
    import asyncpg as _asyncpg

    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    pool = await _asyncpg.create_pool(
        dsn,
        min_size=settings.db_min_pool,
        max_size=settings.db_max_pool,
        command_timeout=30,
        server_settings={"search_path": f"{settings.db_schema},public"},
    )
    logger.info(
        "PostgreSQL pool created: %s@%s:%s/%s (schema=%s, pool=%d–%d)",
        settings.db_user,
        settings.db_host,
        settings.db_port,
        settings.db_name,
        settings.db_schema,
        settings.db_min_pool,
        settings.db_max_pool,
    )
    return pool


# ── SQLite ───────────────────────────────────────────────────

async def _create_sqlite_pool(settings: "Settings") -> "SqlitePool":
    from amiagi.interfaces.web.db.sqlite_pool import SqlitePool as _SqlitePool

    db_path: str = getattr(settings, "db_sqlite_path", "") or "data/web.db"
    # Ensure parent directory exists.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pool = _SqlitePool(db_path)
    # Eagerly open the connection so migrations can run immediately.
    await pool._ensure()
    logger.info("SQLite pool created: %s", db_path)
    return pool


# ── Migrations ───────────────────────────────────────────────

async def run_migrations(pool: Any, *, schema: str = "dbo") -> None:
    """Execute SQL migration files in order.

    Automatically selects the correct migration directory based on pool type.
    """
    from amiagi.interfaces.web.db.sqlite_pool import SqlitePool as _SqlitePool

    if isinstance(pool, _SqlitePool):
        await _run_sqlite_migrations(pool)
    else:
        await _run_pg_migrations(pool, schema=schema)


async def _run_pg_migrations(pool: "asyncpg.Pool", *, schema: str = "dbo") -> None:
    if not _PG_MIGRATIONS_DIR.exists():
        logger.warning("PG migrations directory not found: %s", _PG_MIGRATIONS_DIR)
        return

    migration_files = sorted(_PG_MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.info("No PG migration files found.")
        return

    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"SET search_path TO {schema}, public")

        for mig in migration_files:
            logger.info("Running PG migration: %s", mig.name)
            sql = mig.read_text(encoding="utf-8")
            await conn.execute(sql)
            logger.info("PG migration complete: %s", mig.name)


async def _run_sqlite_migrations(pool: "SqlitePool") -> None:
    if not _SQLITE_MIGRATIONS_DIR.exists():
        logger.warning("SQLite migrations directory not found: %s", _SQLITE_MIGRATIONS_DIR)
        return

    migration_files = sorted(_SQLITE_MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.info("No SQLite migration files found.")
        return

    import aiosqlite

    # Use executescript for multi-statement DDL.
    conn: aiosqlite.Connection = pool._conn  # type: ignore[assignment]
    for mig in migration_files:
        logger.info("Running SQLite migration: %s", mig.name)
        sql = mig.read_text(encoding="utf-8")
        await conn.executescript(sql)
        logger.info("SQLite migration complete: %s", mig.name)


# ── Shutdown ─────────────────────────────────────────────────

async def close_pool(pool: Any) -> None:
    """Gracefully close the connection pool (works for both back-ends)."""
    await pool.close()
    logger.info("Database pool closed.")
