"""Async database repository for SecretVault persistence.

Wraps ``dbo.vault_secrets`` and ``dbo.vault_access_log`` tables created by
migration 009.  Works with both asyncpg (PostgreSQL) and
:class:`SqlitePool` (SQLite) via the unified pool API.

All secret values are Fernet-encrypted **before** reaching this layer — the
repository stores and retrieves opaque ciphertext tokens.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
            """SELECT agent_id, key, secret_type, expires_at, last_access_at
               FROM dbo.vault_secrets
               ORDER BY agent_id, key""",
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            grouped[r["agent_id"]].append(self._serialize_secret_row(r))
        result: list[dict[str, Any]] = []
        for agent_id, keys in grouped.items():
            result.append({"agent_id": agent_id, "keys": keys, "count": len(keys)})
        return result

    async def list_keys(self, agent_id: str, *, include_metadata: bool = False) -> list[str] | list[dict[str, Any]]:
        """Return secret key names for *agent_id* (no values)."""
        rows = await self._pool.fetch(
            """SELECT agent_id, key, secret_type, expires_at, last_access_at
               FROM dbo.vault_secrets
               WHERE agent_id = $1
               ORDER BY key""",
            agent_id,
        )
        if include_metadata:
            return [self._serialize_secret_row(r) for r in rows]
        return [r["key"] for r in rows]

    async def get_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve encrypted token for (agent_id, key). ``None`` if absent."""
        row = await self._pool.fetchrow(
            "SELECT encrypted_value FROM dbo.vault_secrets WHERE agent_id = $1 AND key = $2",
            agent_id,
            key,
        )
        return row["encrypted_value"] if row else None

    async def get_secret_record(self, agent_id: str, key: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """SELECT agent_id, key, encrypted_value, secret_type, expires_at, last_access_at, created_at, updated_at, rotated_at
               FROM dbo.vault_secrets
               WHERE agent_id = $1 AND key = $2""",
            agent_id,
            key,
        )
        if not row:
            return None
        return {
            "encrypted_value": row["encrypted_value"],
            "type": row.get("secret_type") or "api_key",
            "expires_at": self._serialize_ts(row.get("expires_at")),
            "last_access": self._serialize_ts(row.get("last_access_at")),
            "created_at": self._serialize_ts(row.get("created_at")),
            "updated_at": self._serialize_ts(row.get("updated_at")),
            "rotated_at": self._serialize_ts(row.get("rotated_at")),
        }

    async def set_secret(
        self,
        agent_id: str,
        key: str,
        encrypted_value: str,
        *,
        secret_type: str = "api_key",
        expires_at: str | datetime | None = None,
    ) -> None:
        """Upsert a secret (INSERT or UPDATE on conflict)."""
        await self._pool.execute(
            """INSERT INTO dbo.vault_secrets (agent_id, key, encrypted_value, secret_type, expires_at)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (agent_id, key)
               DO UPDATE SET encrypted_value = $3, secret_type = $4, expires_at = $5, updated_at = now()""",
            agent_id,
            key,
            encrypted_value,
            secret_type,
            expires_at,
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
            """SELECT agent_id, key, encrypted_value, secret_type, expires_at, last_access_at, created_at, updated_at, rotated_at
               FROM dbo.vault_secrets
               ORDER BY agent_id, key""",
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            result.setdefault(r["agent_id"], {})[r["key"]] = {
                "encrypted_value": r["encrypted_value"],
                "type": r.get("secret_type") or "api_key",
                "expires_at": self._serialize_ts(r.get("expires_at")),
                "last_access": self._serialize_ts(r.get("last_access_at")),
                "created_at": self._serialize_ts(r.get("created_at")),
                "updated_at": self._serialize_ts(r.get("updated_at")),
                "rotated_at": self._serialize_ts(r.get("rotated_at")),
            }
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
        if key:
            await self._pool.execute(
                """UPDATE dbo.vault_secrets
                   SET last_access_at = now(), updated_at = updated_at
                   WHERE agent_id = $1 AND key = $2""",
                agent_id,
                key,
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

    async def get_secret_access_log(self, agent_id: str, key: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """SELECT agent_id, key, action, performed_by, created_at
               FROM dbo.vault_access_log
               WHERE agent_id = $1 AND key = $2
               ORDER BY created_at DESC
               LIMIT $3""",
            agent_id,
            key,
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

    def _serialize_ts(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return str(value)

    def _secret_status(self, expires_at: Any) -> str:
        if not expires_at:
            return "active"
        value = expires_at
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return "active"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if value <= now:
            return "expired"
        return "expiring" if value <= now + timedelta(days=7) else "active"

    def _serialize_secret_row(self, row: Any) -> dict[str, Any]:
        agent_id = row["agent_id"]
        key = row["key"]
        expires_at = self._serialize_ts(row.get("expires_at"))
        last_access = self._serialize_ts(row.get("last_access_at"))
        return {
            "id": f"{agent_id}:{key}",
            "key": key,
            "type": row.get("secret_type") or "api_key",
            "expires_at": expires_at,
            "last_access": last_access,
            "status": self._secret_status(expires_at),
        }
