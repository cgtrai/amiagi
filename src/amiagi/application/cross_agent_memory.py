"""CrossAgentMemory — shared memory for inter-agent knowledge transfer."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemoryItem:
    """A single finding shared between agents."""

    agent_id: str
    task_id: str
    key_findings: str
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CrossAgentMemory:
    """Thread-safe in-memory store for cross-agent findings.

    Optionally persists to a JSONL file so that memory survives restarts.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._items: list[MemoryItem] = []
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if persist_path is not None:
            self._load(persist_path)

    # ---- public API ----

    def store(self, item: MemoryItem) -> None:
        """Add a memory item."""
        with self._lock:
            self._items.append(item)
            if self._persist_path:
                self._append_to_disk(item)

    def query(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[MemoryItem]:
        """Retrieve matching items (newest first)."""
        with self._lock:
            result = list(self._items)

        if agent_id is not None:
            result = [i for i in result if i.agent_id == agent_id]
        if task_id is not None:
            result = [i for i in result if i.task_id == task_id]
        if tags:
            tag_set = set(tags)
            result = [i for i in result if tag_set & set(i.tags)]

        result.sort(key=lambda i: i.timestamp, reverse=True)
        return result[:limit]

    def relevant_context(
        self,
        *,
        task_id: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> str:
        """Return a formatted text block of relevant findings for injection into prompts."""
        items = self.query(task_id=task_id, tags=tags, limit=limit)
        if not items:
            return ""
        lines = ["[Cross-agent memory — relevant findings]"]
        for item in items:
            lines.append(
                f"- Agent {item.agent_id} (task {item.task_id}): {item.key_findings}"
            )
        return "\n".join(lines)

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # ---- persistence ----

    def _load(self, path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                self._items.append(
                    MemoryItem(
                        agent_id=data["agent_id"],
                        task_id=data["task_id"],
                        key_findings=data["key_findings"],
                        timestamp=data.get("timestamp", 0.0),
                        tags=data.get("tags", []),
                        metadata=data.get("metadata", {}),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue

    def _append_to_disk(self, item: MemoryItem) -> None:
        assert self._persist_path is not None
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "agent_id": item.agent_id,
            "task_id": item.task_id,
            "key_findings": item.key_findings,
            "timestamp": item.timestamp,
            "tags": item.tags,
            "metadata": item.metadata,
        }
        with self._persist_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
