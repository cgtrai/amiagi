"""Persistent storage for the audit retention policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditRetentionStore:
    """Store the global audit retention window in a small JSON file."""

    def __init__(self, file_path: str | Path) -> None:
        self._path = Path(file_path)

    def load(self) -> tuple[bool, int | None]:
        """Return ``(found, retention_days)``.

        ``retention_days`` may be ``None`` to represent the ``forever`` policy.
        """
        if not self._path.exists():
            return False, None

        data: Any = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, None

        value = data.get("retention_days")
        if value in (None, "", "forever", "none", 0, "0"):
            return True, None
        return True, int(value)

    def save(self, retention_days: int | None) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"retention_days": retention_days}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
