"""JSONL lifecycle logger for agent state changes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LifecycleLogger:
    """Appends agent lifecycle events to a JSONL file (``logs/agent_lifecycle.jsonl``)."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def log(
        self,
        *,
        agent_id: str,
        event: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "event": event,
            **(details or {}),
        }
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
