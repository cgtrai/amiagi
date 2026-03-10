"""Tests for PermissionEnforcer (Phase 7)."""

from __future__ import annotations

import pytest

from amiagi.application.permission_enforcer import PermissionEnforcer, EnforcementResult
from amiagi.domain.permission_policy import AgentPermissionPolicy


@pytest.fixture()
def enforcer() -> PermissionEnforcer:
    return PermissionEnforcer()


class TestPermissionEnforcer:
    def test_no_policy_denies(self, enforcer: PermissionEnforcer) -> None:
        result = enforcer.check_tool("unknown_agent", "read_file")
        assert not result.allowed
        assert "no policy" in result.reason

    def test_allowed_tool(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["read_file"]))
        result = enforcer.check_tool("a1", "read_file")
        assert result.allowed

    def test_denied_tool(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["read_file"]))
        result = enforcer.check_tool("a1", "write_file")
        assert not result.allowed

    def test_shell_access_denied(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["*"], shell_access=False))
        result = enforcer.check_tool("a1", "run_shell")
        assert not result.allowed
        assert "shell" in result.reason

    def test_shell_access_allowed(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["*"], shell_access=True))
        result = enforcer.check_tool("a1", "run_shell")
        assert result.allowed

    def test_network_access_denied(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["*"], network_access=False))
        result = enforcer.check_tool("a1", "fetch_web")
        assert not result.allowed
        assert "network" in result.reason

    def test_network_access_allowed(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=["*"], network_access=True))
        result = enforcer.check_tool("a1", "fetch_web")
        assert result.allowed

    def test_check_path_allowed(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_paths=["workspace/*"]))
        result = enforcer.check_path("a1", "workspace/file.txt", write=True)
        assert result.allowed

    def test_check_path_denied(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_paths=["workspace/*"]))
        result = enforcer.check_path("a1", "/etc/passwd", write=True)
        assert not result.allowed

    def test_check_path_denies_protected_system_tools_write(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy.allow_all())
        result = enforcer.check_path("a1", "src/amiagi/system_tools/demo.py", write=True)
        assert not result.allowed
        assert "protected system tools" in result.reason

    def test_check_path_allows_protected_system_tools_read(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy.allow_all())
        result = enforcer.check_path("a1", "src/amiagi/system_tools/demo.py", write=False)
        assert result.allowed

    def test_check_path_no_policy(self, enforcer: PermissionEnforcer) -> None:
        result = enforcer.check_path("a1", "any/path")
        assert not result.allowed

    def test_check_file_size_ok(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(max_file_size_bytes=1000))
        result = enforcer.check_file_size("a1", 500)
        assert result.allowed

    def test_check_file_size_exceeded(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(max_file_size_bytes=1000))
        result = enforcer.check_file_size("a1", 2000)
        assert not result.allowed
        assert "exceeds" in result.reason

    def test_denial_log(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy(allowed_tools=[]))
        enforcer.check_tool("a1", "bad_tool")
        enforcer.check_tool("a1", "other_tool")
        log = enforcer.denial_log()
        assert len(log) == 2

    def test_get_policy(self, enforcer: PermissionEnforcer) -> None:
        policy = AgentPermissionPolicy.allow_all()
        enforcer.set_policy("a1", policy)
        assert enforcer.get_policy("a1") is policy
        assert enforcer.get_policy("a2") is None

    def test_remove_policy(self, enforcer: PermissionEnforcer) -> None:
        enforcer.set_policy("a1", AgentPermissionPolicy())
        assert enforcer.remove_policy("a1")
        assert not enforcer.remove_policy("a1")
