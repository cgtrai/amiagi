"""Workspace manager — auto-provisioning and isolation.

Each user gets their own workspace directory on first login.
Provides isolation checks so user A cannot access user B's workspace.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Standard workspace subdirectories
_WORKSPACE_SUBDIRS = ("plans", "downloads", "results")


class WorkspaceManager:
    """Manages per-user workspace directories."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def ensure_workspace(self, user_id: str, workspace: str = "default") -> Path:
        """Create user workspace directory if it doesn't exist.

        Returns the workspace root path.
        """
        ws_dir = self._base / user_id / workspace
        ws_dir.mkdir(parents=True, exist_ok=True)
        for subdir in _WORKSPACE_SUBDIRS:
            (ws_dir / subdir).mkdir(exist_ok=True)
        return ws_dir

    def workspace_path(self, user_id: str, workspace: str = "default") -> Path:
        return self._base / user_id / workspace

    # ------------------------------------------------------------------
    # Isolation check
    # ------------------------------------------------------------------

    def can_access(self, requesting_user_id: str, target_user_id: str, *, is_admin: bool = False) -> bool:
        """Check whether *requesting_user_id* may access *target_user_id*'s workspace."""
        if is_admin:
            return True
        return requesting_user_id == target_user_id

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def list_workspaces(self, user_id: str) -> list[str]:
        """List workspace names for a user."""
        user_dir = self._base / user_id
        if not user_dir.exists():
            return []
        return [d.name for d in user_dir.iterdir() if d.is_dir()]

    def workspace_size(self, user_id: str, workspace: str = "default") -> int:
        """Total bytes in the workspace."""
        ws_dir = self._base / user_id / workspace
        if not ws_dir.exists():
            return 0
        return sum(f.stat().st_size for f in ws_dir.rglob("*") if f.is_file())
