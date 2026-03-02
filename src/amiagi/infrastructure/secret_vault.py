"""SecretVault — per-agent encrypted credential storage."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path
from typing import Any


class SecretVault:
    """Stores per-agent secrets with basic obfuscation.

    Secrets are stored in a JSON file with agent-level isolation:
    agent A cannot read secrets belonging to agent B.

    .. note::

       This is *not* production-grade encryption — it uses XOR-based
       obfuscation with a derived key.  For real deployments use OS
       keyring or an external vault.
    """

    def __init__(self, vault_path: Path) -> None:
        self._path = vault_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    # ---- public API ----

    def set_secret(self, agent_id: str, key: str, value: str) -> None:
        """Store a secret for *agent_id*."""
        with self._lock:
            bucket = self._data.setdefault(agent_id, {})
            bucket[key] = self._obfuscate(value, agent_id)
            self._save()

    def get_secret(self, agent_id: str, key: str) -> str | None:
        """Retrieve a secret. Returns ``None`` if not found."""
        with self._lock:
            bucket = self._data.get(agent_id, {})
            obfuscated = bucket.get(key)
            if obfuscated is None:
                return None
            return self._deobfuscate(obfuscated, agent_id)

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
            return True

    def list_keys(self, agent_id: str) -> list[str]:
        """List secret key names for *agent_id* (values NOT exposed)."""
        with self._lock:
            return list(self._data.get(agent_id, {}).keys())

    def delete_agent(self, agent_id: str) -> bool:
        """Remove all secrets for *agent_id*."""
        with self._lock:
            if agent_id not in self._data:
                return False
            del self._data[agent_id]
            self._save()
            return True

    # ---- obfuscation (XOR with derived key) ----

    @staticmethod
    def _derive_key(agent_id: str) -> bytes:
        return hashlib.sha256(f"amiagi-vault-{agent_id}".encode()).digest()

    @staticmethod
    def _xor_bytes(data: bytes, key: bytes) -> bytes:
        return bytes(d ^ key[i % len(key)] for i, d in enumerate(data))

    def _obfuscate(self, plaintext: str, agent_id: str) -> str:
        key = self._derive_key(agent_id)
        encrypted = self._xor_bytes(plaintext.encode("utf-8"), key)
        return base64.b64encode(encrypted).decode("ascii")

    def _deobfuscate(self, ciphertext: str, agent_id: str) -> str:
        key = self._derive_key(agent_id)
        encrypted = base64.b64decode(ciphertext)
        return self._xor_bytes(encrypted, key).decode("utf-8")

    # ---- persistence ----

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
