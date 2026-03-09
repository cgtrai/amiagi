"""SecretVault — per-agent encrypted credential storage (Fernet AES-128-CBC).

Supports two persistence back-ends:

* **File-based** (default) — JSON file on disk, suitable for single-user /
  CLI mode.
* **Database-backed** — when a ``db_pool`` (:class:`asyncpg.Pool` or
  :class:`SqlitePool`) is provided, secrets are persisted in
  ``dbo.vault_secrets`` via :class:`VaultRepository`.  The in-memory dict
  acts as a read-cache; all mutations write-through to the DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from amiagi.interfaces.web.db.vault_repository import VaultRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SecretVault:
    """Stores per-agent secrets encrypted with Fernet (AES-128-CBC + HMAC-SHA256).

    Secrets are stored with agent-level isolation:
    agent A cannot read secrets belonging to agent B.

    A Fernet key is persisted alongside the vault file (``<vault>.key``).
    If no key file exists on first run, a new key is generated automatically.

    When *db_pool* is passed (typically wired in ``app.py`` startup),
    :meth:`attach_db` creates a :class:`VaultRepository` and
    :meth:`sync_from_db` loads the DB state into the in-memory cache.
    All subsequent writes go to the DB *and* update the in-memory dict.
    The JSON file is still maintained as a backup / fallback.
    """

    def __init__(self, vault_path: Path, *, fernet_key: bytes | None = None) -> None:
        self._path = vault_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path = vault_path.with_suffix(".key")
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {}
        self._repo: VaultRepository | None = None

        # Resolve Fernet key: explicit > file > generate
        if fernet_key is not None:
            self._fernet = Fernet(fernet_key)
        else:
            self._fernet = Fernet(self._load_or_create_key())

        self._load()

    # ---- DB integration ----

    def attach_db(self, db_pool: Any) -> None:
        """Attach a database pool and create a :class:`VaultRepository`.

        Call this once during web-server startup, **after** migrations
        have been applied.
        """
        from amiagi.interfaces.web.db.vault_repository import VaultRepository
        self._repo = VaultRepository(db_pool)
        logger.info("SecretVault: database persistence attached")

    @property
    def has_db(self) -> bool:
        """True when a DB-backed repository is available."""
        return self._repo is not None

    @property
    def repo(self) -> "VaultRepository | None":
        """Expose the underlying repository for direct async queries."""
        return self._repo

    async def sync_from_db(self) -> int:
        """Load all secrets from DB into the in-memory cache.

        Returns the number of secrets loaded.  No-op when no DB is attached.
        Also back-fills the JSON file as a safety fallback.
        """
        if self._repo is None:
            return 0
        db_data = await self._repo.fetch_all()
        with self._lock:
            self._data = db_data
            self._save()
        count = sum(len(v) for v in db_data.values())
        logger.info("SecretVault: synced %d secrets from database", count)
        return count

    async def migrate_file_to_db(self) -> int:
        """One-time migration: copy file-based secrets into the DB.

        Idempotent — uses UPSERT so existing DB rows are updated.
        Returns the number of secrets migrated.
        """
        if self._repo is None:
            return 0
        count = 0
        with self._lock:
            snapshot = {
                aid: dict(secs) for aid, secs in self._data.items()
            }
        for agent_id, secrets in snapshot.items():
            for key, stored in secrets.items():
                entry = self._coerce_entry(stored)
                if entry is None:
                    continue
                await self._repo.set_secret(
                    agent_id,
                    key,
                    entry["encrypted_value"],
                    secret_type=entry.get("type") or "api_key",
                    expires_at=entry.get("expires_at"),
                )
                count += 1
        logger.info("SecretVault: migrated %d file-based secrets to database", count)
        return count

    # ---- async public API (DB-aware) ----

    async def _aset_secret_with_metadata(
        self,
        agent_id: str,
        key: str,
        value: str,
        *,
        secret_type: str = "api_key",
        expires_at: datetime | str | None = None,
    ) -> None:
        encrypted = self._encrypt(value)
        expires_iso = self._normalize_expires_at(expires_at)
        previous = self._get_entry(agent_id, key)
        entry = {
            "encrypted_value": encrypted,
            "type": secret_type or "api_key",
            "expires_at": expires_iso,
            "last_access": previous.get("last_access"),
            "created_at": previous.get("created_at") or _utc_now().isoformat(),
            "updated_at": _utc_now().isoformat(),
            "rotated_at": previous.get("rotated_at"),
        }
        if self._repo is not None:
            await self._repo.set_secret(
                agent_id,
                key,
                encrypted,
                secret_type=entry["type"],
                expires_at=expires_iso,
            )
        with self._lock:
            self._data.setdefault(agent_id, {})[key] = entry
            self._save()

    async def aset_secret(
        self,
        agent_id: str,
        key: str,
        value: str,
        *,
        secret_type: str = "api_key",
        expires_at: datetime | str | None = None,
    ) -> None:
        """Store a secret — async, DB-first when available."""
        await self._aset_secret_with_metadata(
            agent_id,
            key,
            value,
            secret_type=secret_type,
            expires_at=expires_at,
        )

    async def aget_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve and decrypt — async, reads from cache (populated from DB)."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            entry = self._coerce_entry(bucket.get(key)) if key in bucket else None
        if entry is None:
            # Cache miss — try DB if available
            if self._repo is not None:
                entry = await self._repo.get_secret_record(agent_id, key)
                if entry is not None:
                    with self._lock:
                        self._data.setdefault(agent_id, {})[key] = entry
            if entry is None:
                return None
        self._touch_last_access(agent_id, key)
        return self._decrypt(entry["encrypted_value"])

    async def adelete_secret(self, agent_id: str, key: str) -> bool:
        """Delete — async, DB-first when available."""
        if self._repo is not None:
            removed = await self._repo.delete_secret(agent_id, key)
            if not removed:
                return False
        with self._lock:
            bucket = self._data.get(agent_id)
            if bucket is None or key not in bucket:
                return False if self._repo is None else True
            del bucket[key]
            if not bucket:
                del self._data[agent_id]
            self._save()
        return True

    async def arotate_secret(self, agent_id: str, key: str, new_value: str) -> bool:
        """Rotate — async, updates DB rotated_at timestamp."""
        encrypted = self._encrypt(new_value)
        if self._repo is not None:
            ok = await self._repo.rotate_secret(agent_id, key, encrypted)
            if not ok:
                return False
        with self._lock:
            bucket = self._data.get(agent_id, {})
            if key not in bucket and self._repo is None:
                return False
            previous = self._get_entry(agent_id, key)
            self._data.setdefault(agent_id, {})[key] = {
                "encrypted_value": encrypted,
                "type": previous.get("type") or "api_key",
                "expires_at": previous.get("expires_at"),
                "last_access": previous.get("last_access"),
                "created_at": previous.get("created_at") or _utc_now().isoformat(),
                "updated_at": _utc_now().isoformat(),
                "rotated_at": _utc_now().isoformat(),
            }
            self._save()
        return True

    async def alist_agents(self) -> list[dict]:
        """List agents — async, DB-first when available."""
        if self._repo is not None:
            return await self._repo.list_agents()
        return self.list_agents()

    async def alist_keys(self, agent_id: str, *, include_metadata: bool = False) -> list[str] | list[dict[str, Any]]:
        """List keys — async, DB-first when available."""
        if self._repo is not None:
            return await self._repo.list_keys(agent_id, include_metadata=include_metadata)
        return self.list_keys(agent_id, include_metadata=include_metadata)

    async def adelete_agent(self, agent_id: str) -> bool:
        """Delete all secrets for agent — async."""
        if self._repo is not None:
            removed = await self._repo.delete_agent(agent_id)
            if not removed:
                return False
        with self._lock:
            if agent_id not in self._data:
                return False if self._repo is None else True
            del self._data[agent_id]
            self._save()
        return True

    async def alog_access(
        self,
        agent_id: str,
        key: str | None,
        action: str,
        performed_by: str | None = None,
    ) -> None:
        """Write a vault access log entry to the DB (if available)."""
        if self._repo is not None:
            await self._repo.log_access(agent_id, key, action, performed_by)
        if key:
            self._touch_last_access(agent_id, key)

    async def aget_access_log(self, *, limit: int = 50) -> list[dict]:
        """Return recent access log entries from DB."""
        if self._repo is not None:
            return await self._repo.get_access_log(limit=limit)
        return []

    async def aget_secret_access_log(self, agent_id: str, key: str, *, limit: int = 50) -> list[dict]:
        """Return recent access log entries for one secret."""
        if self._repo is not None:
            return await self._repo.get_secret_access_log(agent_id, key, limit=limit)
        return []

    # ---- sync public API (backward compat for CLI / non-web callers) ----

    def set_secret(
        self,
        agent_id: str,
        key: str,
        value: str,
        *,
        secret_type: str = "api_key",
        expires_at: datetime | str | None = None,
    ) -> None:
        """Store a secret for *agent_id* (Fernet-encrypted).

        When DB is attached, schedules an async write on the running loop.
        """
        encrypted = self._encrypt(value)
        expires_iso = self._normalize_expires_at(expires_at)
        if self._repo is not None:
            self._schedule_db(
                self._repo.set_secret,
                agent_id,
                key,
                encrypted,
                kwargs={"secret_type": secret_type, "expires_at": expires_iso},
            )
        with self._lock:
            previous = self._get_entry(agent_id, key)
            bucket = self._data.setdefault(agent_id, {})
            bucket[key] = {
                "encrypted_value": encrypted,
                "type": secret_type or previous.get("type") or "api_key",
                "expires_at": expires_iso,
                "last_access": previous.get("last_access"),
                "created_at": previous.get("created_at") or _utc_now().isoformat(),
                "updated_at": _utc_now().isoformat(),
                "rotated_at": previous.get("rotated_at"),
            }
            self._save()

    def get_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve and decrypt a secret. Returns ``None`` if not found."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            entry = self._coerce_entry(bucket.get(key)) if key in bucket else None
            if entry is None:
                return None
        self._touch_last_access(agent_id, key)
        return self._decrypt(entry["encrypted_value"])

    def delete_secret(self, agent_id: str, key: str) -> bool:
        """Remove a secret. Returns ``True`` if removed."""
        with self._lock:
            bucket = self._data.get(agent_id)
            if bucket is None or key not in bucket:
                return False
            del bucket[key]
            if not bucket:
                del self._data[agent_id]
            self._save()
        if self._repo is not None:
            self._schedule_db(self._repo.delete_secret, agent_id, key)
        return True

    def list_keys(self, agent_id: str, *, include_metadata: bool = False) -> list[str] | list[dict[str, Any]]:
        """List secret key names for *agent_id* (values NOT exposed)."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            if not include_metadata:
                return list(bucket.keys())
            return [self._public_entry(agent_id, key, stored) for key, stored in bucket.items()]

    def list_agents(self) -> list[dict]:
        """Return a summary of all agents that have secrets stored.

        Each entry contains ``agent_id``, ``keys``, and ``count``.
        """
        with self._lock:
            result: list[dict] = []
            for agent_id, secrets in self._data.items():
                result.append({
                    "agent_id": agent_id,
                    "keys": [self._public_entry(agent_id, key, stored) for key, stored in secrets.items()],
                    "count": len(secrets),
                })
            return result

    def delete_agent(self, agent_id: str) -> bool:
        """Remove all secrets for *agent_id*."""
        with self._lock:
            if agent_id not in self._data:
                return False
            del self._data[agent_id]
            self._save()
        if self._repo is not None:
            self._schedule_db(self._repo.delete_agent, agent_id)
        return True

    # ---- Fernet encryption ----

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt *plaintext* with Fernet and return URL-safe base64 token."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def _decrypt(self, token: str) -> str:
        """Decrypt a Fernet token back to plaintext."""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning("Failed to decrypt vault token — key may have rotated")
            raise

    # ---- key management ----

    def _load_or_create_key(self) -> bytes:
        """Load the Fernet key from disk, or generate + persist a new one."""
        if self._key_path.exists():
            return self._key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self._key_path.write_bytes(key)
        logger.info("Generated new Fernet encryption key: %s", self._key_path)
        return key

    # ---- file persistence ----

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- internal helpers ----

    def _normalize_expires_at(self, expires_at: datetime | str | None) -> str | None:
        if expires_at is None:
            return None
        if isinstance(expires_at, str):
            value = expires_at.strip()
            return value or None
        return expires_at.astimezone(timezone.utc).isoformat()

    def _coerce_entry(self, stored: Any) -> dict[str, Any] | None:
        if stored is None:
            return None
        if isinstance(stored, str):
            return {
                "encrypted_value": stored,
                "type": "api_key",
                "expires_at": None,
                "last_access": None,
                "created_at": None,
                "updated_at": None,
                "rotated_at": None,
            }
        if isinstance(stored, dict):
            entry = dict(stored)
            entry.setdefault("encrypted_value", "")
            entry.setdefault("type", "api_key")
            entry.setdefault("expires_at", None)
            entry.setdefault("last_access", None)
            entry.setdefault("created_at", None)
            entry.setdefault("updated_at", None)
            entry.setdefault("rotated_at", None)
            return entry
        return None

    def _get_entry(self, agent_id: str, key: str) -> dict[str, Any]:
        bucket = self._data.get(agent_id, {})
        return self._coerce_entry(bucket.get(key)) or {
            "encrypted_value": "",
            "type": "api_key",
            "expires_at": None,
            "last_access": None,
            "created_at": None,
            "updated_at": None,
            "rotated_at": None,
        }

    def _secret_status(self, entry: dict[str, Any]) -> str:
        expires_at = entry.get("expires_at")
        if not expires_at:
            return "active"
        try:
            expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            return "active"
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        now = _utc_now()
        if expires_dt <= now:
            return "expired"
        if expires_dt <= now + timedelta(days=7):
            return "expiring"
        return "active"

    def _public_entry(self, agent_id: str, key: str, stored: Any) -> dict[str, Any]:
        entry = self._coerce_entry(stored) or {}
        return {
            "id": f"{agent_id}:{key}",
            "key": key,
            "type": entry.get("type") or "api_key",
            "expires_at": entry.get("expires_at"),
            "last_access": entry.get("last_access"),
            "status": self._secret_status(entry),
        }

    def _touch_last_access(self, agent_id: str, key: str) -> None:
        with self._lock:
            bucket = self._data.get(agent_id)
            if bucket is None or key not in bucket:
                return
            entry = self._coerce_entry(bucket.get(key))
            if entry is None:
                return
            entry["last_access"] = _utc_now().isoformat()
            bucket[key] = entry
            self._save()

    def _schedule_db(self, coro_func: Any, *args: Any, kwargs: dict[str, Any] | None = None) -> None:
        """Fire-and-forget an async DB write from sync context.

        Silently no-ops when no DB is attached or no running event loop.
        """
        if self._repo is None or coro_func is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_func(*args, **(kwargs or {})))
        except RuntimeError:
            # No running event loop (e.g. CLI / test context) — skip DB write
            pass
