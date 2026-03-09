"""SandboxManager — per-agent isolated working directories."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any


class SandboxManager:
    """Creates and manages isolated working directories per agent.

    Each sandbox is a subdirectory of *root*.  Agents can only access
    files within their own sandbox unless explicitly allowed.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sandboxes: dict[str, Path] = {}

    @property
    def root(self) -> Path:
        return self._root

    def create(self, agent_id: str) -> Path:
        """Create a sandbox directory for *agent_id* and return its path."""
        with self._lock:
            sandbox_dir = self._root / agent_id
            sandbox_dir.mkdir(parents=True, exist_ok=True)
            self._sandboxes[agent_id] = sandbox_dir
            return sandbox_dir

    def get(self, agent_id: str) -> Path | None:
        """Return sandbox path for *agent_id* or ``None``."""
        return self._sandboxes.get(agent_id)

    def destroy(self, agent_id: str) -> bool:
        """Remove the sandbox directory for *agent_id*. Returns ``True`` if removed."""
        with self._lock:
            path = self._sandboxes.pop(agent_id, None)
            if path is None:
                return False
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            return True

    def list_sandboxes(self) -> dict[str, Path]:
        """Return all registered sandboxes."""
        return dict(self._sandboxes)

    def resolve_path(self, agent_id: str, relative_path: str) -> Path | None:
        """Resolve *relative_path* inside the agent's sandbox.

        Returns ``None`` if the resolved path escapes the sandbox root
        (path traversal prevention).
        """
        sandbox = self._sandboxes.get(agent_id)
        if sandbox is None:
            return None
        resolved = (sandbox / relative_path).resolve()
        if not str(resolved).startswith(str(sandbox.resolve())):
            return None  # path traversal
        return resolved

    def sandbox_size(self, agent_id: str) -> int:
        """Total size in bytes of files in the agent's sandbox."""
        sandbox = self._sandboxes.get(agent_id)
        if sandbox is None or not sandbox.exists():
            return 0
        total = 0
        for dirpath, _dirnames, filenames in os.walk(sandbox):
            for f in filenames:
                total += (Path(dirpath) / f).stat().st_size
        return total

    def list_files(self, agent_id: str) -> list[Path]:
        """Return top-level files and folders for *agent_id* sorted by name."""
        sandbox = self._sandboxes.get(agent_id)
        if sandbox is None or not sandbox.exists():
            return []
        return sorted(sandbox.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
