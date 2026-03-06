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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from amiagi.interfaces.web.db.vault_repository import VaultRepository

logger = logging.getLogger(__name__)


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
            for key, encrypted_value in secrets.items():
                await self._repo.set_secret(agent_id, key, encrypted_value)
                count += 1
        logger.info("SecretVault: migrated %d file-based secrets to database", count)
        return count

    # ---- async public API (DB-aware) ----

    async def aset_secret(self, agent_id: str, key: str, value: str) -> None:
        """Store a secret — async, DB-first when available."""
        encrypted = self._encrypt(value)
        if self._repo is not None:
            await self._repo.set_secret(agent_id, key, encrypted)
        with self._lock:
            self._data.setdefault(agent_id, {})[key] = encrypted
            self._save()

    async def aget_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve and decrypt — async, reads from cache (populated from DB)."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            token = bucket.get(key)
        if token is None:
            # Cache miss — try DB if available
            if self._repo is not None:
                token = await self._repo.get_secret(agent_id, key)
                if token is not None:
                    with self._lock:
                        self._data.setdefault(agent_id, {})[key] = token
            if token is None:
                return None
        return self._decrypt(token)

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
            self._data.setdefault(agent_id, {})[key] = encrypted
            self._save()
        return True

    async def alist_agents(self) -> list[dict]:
        """List agents — async, DB-first when available."""
        if self._repo is not None:
            return await self._repo.list_agents()
        return self.list_agents()

    async def alist_keys(self, agent_id: str) -> list[str]:
        """List keys — async, DB-first when available."""
        if self._repo is not None:
            return await self._repo.list_keys(agent_id)
        return self.list_keys(agent_id)

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

    async def aget_access_log(self, *, limit: int = 50) -> list[dict]:
        """Return recent access log entries from DB."""
        if self._repo is not None:
            return await self._repo.get_access_log(limit=limit)
        return []

    # ---- sync public API (backward compat for CLI / non-web callers) ----

    def set_secret(self, agent_id: str, key: str, value: str) -> None:
        """Store a secret for *agent_id* (Fernet-encrypted).

        When DB is attached, schedules an async write on the running loop.
        """
        encrypted = self._encrypt(value)
        if self._repo is not None:
            self._schedule_db(self._repo.set_secret, agent_id, key, encrypted)
        with self._lock:
            bucket = self._data.setdefault(agent_id, {})
            bucket[key] = encrypted
            self._save()

    def get_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve and decrypt a secret. Returns ``None`` if not found."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            token = bucket.get(key)
            if token is None:
                return None
            return self._decrypt(token)

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

    def list_keys(self, agent_id: str) -> list[str]:
        """List secret key names for *agent_id* (values NOT exposed)."""
        with self._lock:
            return list(self._data.get(agent_id, {}).keys())

    def list_agents(self) -> list[dict]:
        """Return a summary of all agents that have secrets stored.

        Each entry contains ``agent_id``, ``keys``, and ``count``.
        """
        with self._lock:
            result: list[dict] = []
            for agent_id, secrets in self._data.items():
                result.append({
                    "agent_id": agent_id,
                    "keys": list(secrets.keys()),
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

    def _schedule_db(self, coro_func: Any, *args: Any) -> None:
        """Fire-and-forget an async DB write from sync context.

        Silently no-ops when no DB is attached or no running event loop.
        """
        if self._repo is None or coro_func is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_func(*args))
        except RuntimeError:
            # No running event loop (e.g. CLI / test context) — skip DB write
            pass
