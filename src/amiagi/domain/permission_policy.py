"""AgentPermissionPolicy — per-agent permission descriptor."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentPermissionPolicy:
    """Defines what a specific agent is allowed to do.

    Attributes:
        allowed_tools: tool names the agent may invoke (empty = all denied).
        denied_tools: explicit deny list (takes precedence over allowed).
        allowed_paths: glob patterns for filesystem paths the agent may access.
        read_only_paths: globs the agent may read but not write.
        network_access: whether the agent may make outbound calls.
        shell_access: whether the agent may use ``run_shell``.
        max_file_size_bytes: maximum file size the agent may create/write.
    """

    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    read_only_paths: list[str] = field(default_factory=list)
    network_access: bool = False
    shell_access: bool = False
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB default

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check whether *tool_name* is permitted by this policy."""
        if tool_name in self.denied_tools:
            return False
        if not self.allowed_tools:
            # empty allow list = deny by default
            return False
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def is_path_allowed(self, path: str, *, write: bool = False) -> bool:
        """Check whether *path* matches allowed or read_only patterns."""
        if write:
            return self._matches_any(path, self.allowed_paths)
        return self._matches_any(path, self.allowed_paths) or self._matches_any(
            path, self.read_only_paths
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "allowed_paths": self.allowed_paths,
            "read_only_paths": self.read_only_paths,
            "network_access": self.network_access,
            "shell_access": self.shell_access,
            "max_file_size_bytes": self.max_file_size_bytes,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AgentPermissionPolicy":
        return AgentPermissionPolicy(
            allowed_tools=data.get("allowed_tools", []),
            denied_tools=data.get("denied_tools", []),
            allowed_paths=data.get("allowed_paths", []),
            read_only_paths=data.get("read_only_paths", []),
            network_access=data.get("network_access", False),
            shell_access=data.get("shell_access", False),
            max_file_size_bytes=data.get("max_file_size_bytes", 10 * 1024 * 1024),
        )

    @staticmethod
    def allow_all() -> "AgentPermissionPolicy":
        """Convenience: a policy that allows everything (for trusted agents)."""
        return AgentPermissionPolicy(
            allowed_tools=["*"],
            allowed_paths=["**/*"],
            read_only_paths=[],
            network_access=True,
            shell_access=True,
        )

    # ---- internals ----

    @staticmethod
    def _matches_any(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(path, p) for p in patterns)
