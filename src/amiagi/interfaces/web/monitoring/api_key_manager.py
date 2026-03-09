"""API key management — self-service API key creation and validation.

Table: ``dbo.api_keys`` — user-managed API keys with scopes and expiry.
Keys are stored as SHA-256 hashes. The raw key is only shown once at creation.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ak_"
_KEY_BYTES = 32


@dataclass
class ApiKeyRecord:
    id: str
    user_id: str
    name: str
    scopes: list[str]
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    rate_limit_per_min: int | None
    created_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "prefix": self.id[:8],
            "scopes": self.scopes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "rate_limit_per_min": self.rate_limit_per_min,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_key(row) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        name=row["name"],
        scopes=list(row["scopes"] or []),
        expires_at=row.get("expires_at"),
        is_active=row["is_active"],
        last_used_at=row.get("last_used_at"),
        rate_limit_per_min=row.get("rate_limit_per_min"),
        created_at=row.get("created_at"),
    )


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new raw API key."""
    return _KEY_PREFIX + secrets.token_hex(_KEY_BYTES)


class ApiKeyManager:
    """CRUD and validation for API keys."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def create_key(
        self, user_id: str, name: str,
        scopes: list[str] | None = None,
        expires_at: datetime | None = None,
        rate_limit_per_min: int | None = None,
    ) -> tuple[str, ApiKeyRecord]:
        """Create a new API key. Returns (raw_key, record)."""
        raw_key = generate_api_key()
        key_hash = _hash_key(raw_key)
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.api_keys (user_id, name, key_hash, scopes, expires_at, rate_limit_per_min)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            user_id, name, key_hash, scopes or [], expires_at, rate_limit_per_min,
        )
        return raw_key, _row_to_key(row)

    async def validate_key(self, raw_key: str) -> ApiKeyRecord | None:
        """Validate an API key. Returns the record if valid, None otherwise."""
        key_hash = _hash_key(raw_key)
        row = await self._pool.fetchrow(
            """
            SELECT * FROM dbo.api_keys
            WHERE key_hash = $1 AND is_active = true
              AND (expires_at IS NULL OR expires_at > now())
            """,
            key_hash,
        )
        if not row:
            return None
        # Update last_used_at
        await self._pool.execute(
            "UPDATE dbo.api_keys SET last_used_at = now() WHERE id = $1::uuid",
            str(row["id"]),
        )
        return _row_to_key(row)

    async def list_keys(self, user_id: str) -> list[ApiKeyRecord]:
        rows = await self._pool.fetch(
            "SELECT * FROM dbo.api_keys WHERE user_id = $1::uuid ORDER BY created_at DESC",
            user_id,
        )
        return [_row_to_key(r) for r in rows]

    async def revoke_key(self, key_id: str) -> bool:
        result = await self._pool.execute(
            "UPDATE dbo.api_keys SET is_active = false WHERE id = $1::uuid", key_id
        )
        return "UPDATE 1" in result

    async def delete_key(self, key_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.api_keys WHERE id = $1::uuid", key_id
        )
        return result.endswith("1")
