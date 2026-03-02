"""AuditChain — immutable audit log for all system actions."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    """A single auditable action in the system."""

    agent_id: str
    action: str
    target: str
    timestamp: float = field(default_factory=time.time)
    approved_by: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    outcome: str = "ok"  # "ok" | "denied" | "error"


class AuditChain:
    """Append-only audit log persisted to JSONL.

    Every action performed by an agent is recorded with who ordered it,
    who approved it, and the outcome.  The log is append-only and never
    rewritten — suitable for compliance auditing.
    """

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        """Append an audit entry."""
        with self._lock:
            self._entries.append(entry)
            self._append_to_disk(entry)

    def record_action(
        self,
        *,
        agent_id: str,
        action: str,
        target: str,
        approved_by: str = "",
        details: dict[str, Any] | None = None,
        outcome: str = "ok",
    ) -> AuditEntry:
        """Convenience: create and record an entry in one call."""
        entry = AuditEntry(
            agent_id=agent_id,
            action=action,
            target=target,
            approved_by=approved_by,
            details=details or {},
            outcome=outcome,
        )
        self.record(entry)
        return entry

    def query(
        self,
        *,
        agent_id: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Return filtered audit entries (newest first)."""
        with self._lock:
            result = list(self._entries)

        if agent_id is not None:
            result = [e for e in result if e.agent_id == agent_id]
        if action is not None:
            result = [e for e in result if e.action == action]
        if outcome is not None:
            result = [e for e in result if e.outcome == outcome]

        result.sort(key=lambda e: e.timestamp, reverse=True)
        return result[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    # ---- persistence ----

    def _append_to_disk(self, entry: AuditEntry) -> None:
        record: dict[str, Any] = {
            "agent_id": entry.agent_id,
            "action": entry.action,
            "target": entry.target,
            "timestamp": entry.timestamp,
            "approved_by": entry.approved_by,
            "details": entry.details,
            "outcome": entry.outcome,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
