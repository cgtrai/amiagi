"""Async PostgreSQL connection pool management for the web interface."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

    from amiagi.config import Settings

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def create_pool(settings: "Settings") -> "asyncpg.Pool":
    """Create and return an asyncpg connection pool, setting search_path to the configured schema."""
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


async def run_migrations(pool: "asyncpg.Pool", *, schema: str = "dbo") -> None:
    """Execute SQL migration files in order from the migrations directory."""
    if not _MIGRATIONS_DIR.exists():
        logger.warning("Migrations directory not found: %s", _MIGRATIONS_DIR)
        return

    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.info("No migration files found.")
        return

    async with pool.acquire() as conn:
        # Ensure schema exists
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"SET search_path TO {schema}, public")

        for mig in migration_files:
            logger.info("Running migration: %s", mig.name)
            sql = mig.read_text(encoding="utf-8")
            await conn.execute(sql)
            logger.info("Migration complete: %s", mig.name)


async def close_pool(pool: "asyncpg.Pool") -> None:
    """Gracefully close the connection pool."""
    await pool.close()
    logger.info("PostgreSQL pool closed.")
