"""SharedWorkspace — per-project shared directory with authorship tracking."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileChange:
    """Record of a single file modification."""

    path: str
    agent_id: str
    action: str  # "write" | "append" | "delete"
    timestamp: float = field(default_factory=time.time)
    size_bytes: int = 0


class SharedWorkspace:
    """Thread-safe shared filesystem for agents within a project.

    Each file operation is recorded in a change log so that authorship
    can be audited later.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._changes: list[FileChange] = []
        self._log_path = root / ".workspace_log.jsonl"

    @property
    def root(self) -> Path:
        return self._root

    # ---- file operations ----

    def write_file(self, relative_path: str, content: str, *, agent_id: str) -> Path:
        """Write *content* to *relative_path* (overwrite if exists)."""
        target = self._resolve(relative_path)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            change = FileChange(
                path=relative_path,
                agent_id=agent_id,
                action="write",
                size_bytes=len(content.encode("utf-8")),
            )
            self._changes.append(change)
            self._append_log(change)
        return target

    def append_file(self, relative_path: str, content: str, *, agent_id: str) -> Path:
        """Append *content* to *relative_path*."""
        target = self._resolve(relative_path)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
            change = FileChange(
                path=relative_path,
                agent_id=agent_id,
                action="append",
                size_bytes=len(content.encode("utf-8")),
            )
            self._changes.append(change)
            self._append_log(change)
        return target

    def read_file(self, relative_path: str) -> str | None:
        """Return file content or ``None`` if the file does not exist."""
        target = self._resolve(relative_path)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def delete_file(self, relative_path: str, *, agent_id: str) -> bool:
        """Delete a file. Returns ``True`` if the file was removed."""
        target = self._resolve(relative_path)
        with self._lock:
            if not target.exists():
                return False
            target.unlink()
            change = FileChange(
                path=relative_path,
                agent_id=agent_id,
                action="delete",
            )
            self._changes.append(change)
            self._append_log(change)
            return True

    def list_files(self) -> list[str]:
        """List all files relative to workspace root (excluding log)."""
        result: list[str] = []
        for p in sorted(self._root.rglob("*")):
            if p.is_file() and p.name != ".workspace_log.jsonl":
                result.append(str(p.relative_to(self._root)))
        return result

    # ---- history ----

    def changes(self, *, agent_id: str | None = None) -> list[FileChange]:
        """Return change log, optionally filtered by *agent_id*."""
        if agent_id is None:
            return list(self._changes)
        return [c for c in self._changes if c.agent_id == agent_id]

    def last_author(self, relative_path: str) -> str | None:
        """Return agent_id of the last writer of *relative_path*."""
        for change in reversed(self._changes):
            if change.path == relative_path and change.action != "delete":
                return change.agent_id
        return None

    # ---- internals ----

    def _resolve(self, relative_path: str) -> Path:
        resolved = (self._root / relative_path).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(f"Path traversal detected: {relative_path}")
        return resolved

    def _append_log(self, change: FileChange) -> None:
        entry: dict[str, Any] = {
            "path": change.path,
            "agent_id": change.agent_id,
            "action": change.action,
            "timestamp": change.timestamp,
            "size_bytes": change.size_bytes,
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
