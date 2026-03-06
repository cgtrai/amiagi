"""Async database repository for SecretVault persistence.

Wraps ``dbo.vault_secrets`` and ``dbo.vault_access_log`` tables created by
migration 009.  Works with both asyncpg (PostgreSQL) and
:class:`SqlitePool` (SQLite) via the unified pool API.

All secret values are Fernet-encrypted **before** reaching this layer — the
repository stores and retrieves opaque ciphertext tokens.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from amiagi.interfaces.web.db.pool import DbPool

logger = logging.getLogger(__name__)


class VaultRepository:
    """Async CRUD for ``dbo.vault_secrets`` and ``dbo.vault_access_log``."""

    def __init__(self, pool: "DbPool") -> None:
        self._pool = pool

    # ── Secret CRUD ──────────────────────────────────────────

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return summary per agent: ``[{agent_id, keys, count}, ...]``."""
        rows = await self._pool.fetch(
            """SELECT agent_id, array_agg(key ORDER BY key) AS keys, count(*) AS cnt
               FROM dbo.vault_secrets
               GROUP BY agent_id
               ORDER BY agent_id""",
        )
        result: list[dict[str, Any]] = []
        for r in rows:
            keys = r["keys"]
            # SQLite returns JSON-encoded array string; asyncpg returns list
            if isinstance(keys, str):
                import json
                keys = json.loads(keys)
            result.append({
                "agent_id": r["agent_id"],
                "keys": keys,
                "count": r["cnt"],
            })
        return result

    async def list_keys(self, agent_id: str) -> list[str]:
        """Return secret key names for *agent_id* (no values)."""
        rows = await self._pool.fetch(
            "SELECT key FROM dbo.vault_secrets WHERE agent_id = $1 ORDER BY key",
            agent_id,
        )
        return [r["key"] for r in rows]

    async def get_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve encrypted token for (agent_id, key). ``None`` if absent."""
        row = await self._pool.fetchrow(
            "SELECT encrypted_value FROM dbo.vault_secrets WHERE agent_id = $1 AND key = $2",
            agent_id,
            key,
        )
        return row["encrypted_value"] if row else None

    async def set_secret(self, agent_id: str, key: str, encrypted_value: str) -> None:
        """Upsert a secret (INSERT or UPDATE on conflict)."""
        await self._pool.execute(
            """INSERT INTO dbo.vault_secrets (agent_id, key, encrypted_value)
               VALUES ($1, $2, $3)
               ON CONFLICT (agent_id, key)
               DO UPDATE SET encrypted_value = $3, updated_at = now()""",
            agent_id,
            key,
            encrypted_value,
        )

    async def rotate_secret(
        self, agent_id: str, key: str, encrypted_value: str,
    ) -> bool:
        """Update a secret and set ``rotated_at``. Returns False if not found."""
        tag = await self._pool.execute(
            """UPDATE dbo.vault_secrets
               SET encrypted_value = $3, updated_at = now(), rotated_at = now()
               WHERE agent_id = $1 AND key = $2""",
            agent_id,
            key,
            encrypted_value,
        )
        # asyncpg returns e.g. "UPDATE 1"; sqlite wrapper similar
        return "0" not in tag.split()[-1:]

    async def delete_secret(self, agent_id: str, key: str) -> bool:
        """Delete a single secret. Returns True if removed."""
        tag = await self._pool.execute(
            "DELETE FROM dbo.vault_secrets WHERE agent_id = $1 AND key = $2",
            agent_id,
            key,
        )
        return "0" not in tag.split()[-1:]

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete all secrets for *agent_id*."""
        tag = await self._pool.execute(
            "DELETE FROM dbo.vault_secrets WHERE agent_id = $1",
            agent_id,
        )
        return "0" not in tag.split()[-1:]

    async def fetch_all(self) -> dict[str, dict[str, str]]:
        """Load every secret into ``{agent_id: {key: encrypted_value}}``."""
        rows = await self._pool.fetch(
            "SELECT agent_id, key, encrypted_value FROM dbo.vault_secrets ORDER BY agent_id, key",
        )
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result.setdefault(r["agent_id"], {})[r["key"]] = r["encrypted_value"]
        return result

    # ── Access log ───────────────────────────────────────────

    async def log_access(
        self,
        agent_id: str,
        key: str | None,
        action: str,
        performed_by: str | None = None,
    ) -> None:
        """Write one entry to ``dbo.vault_access_log``."""
        await self._pool.execute(
            """INSERT INTO dbo.vault_access_log (agent_id, key, action, performed_by)
               VALUES ($1, $2, $3, $4)""",
            agent_id,
            key,
            action,
            performed_by,
        )

    async def get_access_log(
        self, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent vault access log entries."""
        rows = await self._pool.fetch(
            """SELECT agent_id, key, action, performed_by, created_at
               FROM dbo.vault_access_log
               ORDER BY created_at DESC
               LIMIT $1""",
            limit,
        )
        return [
            {
                "agent_id": r["agent_id"],
                "key": r["key"],
                "action": r["action"],
                "user": r.get("performed_by", ""),
                "timestamp": str(r["created_at"]),
            }
            for r in rows
        ]
