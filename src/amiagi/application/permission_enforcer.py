"""PermissionEnforcer — middleware that checks agent permissions before tool execution."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from amiagi.domain.permission_policy import AgentPermissionPolicy

logger = logging.getLogger(__name__)


@dataclass
class EnforcementResult:
    """Outcome of a permission check."""

    allowed: bool
    reason: str = ""
    agent_id: str = ""
    tool_name: str = ""
    timestamp: float = field(default_factory=time.time)


class PermissionEnforcer:
    """Enforces per-agent permission policies.

    Register a policy for each agent via :meth:`set_policy`.  Then call
    :meth:`check_tool` or :meth:`check_path` before executing an action.
    Denied actions are logged and returned with a reason.
    """

    def __init__(self) -> None:
        self._policies: dict[str, AgentPermissionPolicy] = {}
        self._denial_log: list[EnforcementResult] = []

    def set_policy(self, agent_id: str, policy: AgentPermissionPolicy) -> None:
        """Register or replace the policy for *agent_id*."""
        self._policies[agent_id] = policy

    def get_policy(self, agent_id: str) -> AgentPermissionPolicy | None:
        return self._policies.get(agent_id)

    def remove_policy(self, agent_id: str) -> bool:
        return self._policies.pop(agent_id, None) is not None

    # ---- checks ----

    def check_tool(self, agent_id: str, tool_name: str) -> EnforcementResult:
        """Check whether *agent_id* is allowed to invoke *tool_name*."""
        policy = self._policies.get(agent_id)
        if policy is None:
            # No policy registered → deny by default
            return self._deny(agent_id, tool_name, "no policy registered for agent")

        # Special shell check
        if tool_name == "run_shell" and not policy.shell_access:
            return self._deny(agent_id, tool_name, "shell access denied by policy")

        # Network tools
        if tool_name in ("fetch_web", "search_web", "download_file") and not policy.network_access:
            return self._deny(agent_id, tool_name, "network access denied by policy")

        if not policy.is_tool_allowed(tool_name):
            return self._deny(agent_id, tool_name, f"tool '{tool_name}' not in allowed list")

        return EnforcementResult(allowed=True, agent_id=agent_id, tool_name=tool_name)

    def check_path(
        self, agent_id: str, path: str, *, write: bool = False
    ) -> EnforcementResult:
        """Check whether *agent_id* may access *path* (read or write)."""
        policy = self._policies.get(agent_id)
        if policy is None:
            return self._deny(agent_id, f"path:{path}", "no policy registered for agent")

        if not policy.is_path_allowed(path, write=write):
            action = "write" if write else "read"
            return self._deny(agent_id, f"path:{path}", f"{action} access denied for path")

        return EnforcementResult(allowed=True, agent_id=agent_id, tool_name=f"path:{path}")

    def check_file_size(self, agent_id: str, size_bytes: int) -> EnforcementResult:
        """Check file size against agent's policy limit."""
        policy = self._policies.get(agent_id)
        if policy is None:
            return self._deny(agent_id, "file_size", "no policy registered")

        if size_bytes > policy.max_file_size_bytes:
            return self._deny(
                agent_id,
                "file_size",
                f"file size {size_bytes} exceeds limit {policy.max_file_size_bytes}",
            )
        return EnforcementResult(allowed=True, agent_id=agent_id, tool_name="file_size")

    # ---- log ----

    def denial_log(self) -> list[EnforcementResult]:
        """Return all denial events."""
        return list(self._denial_log)

    # ---- internals ----

    def _deny(self, agent_id: str, tool_name: str, reason: str) -> EnforcementResult:
        result = EnforcementResult(
            allowed=False,
            reason=reason,
            agent_id=agent_id,
            tool_name=tool_name,
        )
        self._denial_log.append(result)
        logger.warning("Permission denied: agent=%s tool=%s reason=%s", agent_id, tool_name, reason)
        return result
