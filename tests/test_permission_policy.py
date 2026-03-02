"""Tests for AgentPermissionPolicy (Phase 7)."""

from __future__ import annotations

import pytest

from amiagi.domain.permission_policy import AgentPermissionPolicy


class TestAgentPermissionPolicy:
    def test_default_denies_everything(self) -> None:
        policy = AgentPermissionPolicy()
        assert not policy.is_tool_allowed("any_tool")
        assert not policy.network_access
        assert not policy.shell_access

    def test_explicit_allow(self) -> None:
        policy = AgentPermissionPolicy(allowed_tools=["read_file", "write_file"])
        assert policy.is_tool_allowed("read_file")
        assert policy.is_tool_allowed("write_file")
        assert not policy.is_tool_allowed("run_shell")

    def test_wildcard_allow(self) -> None:
        policy = AgentPermissionPolicy(allowed_tools=["*"])
        assert policy.is_tool_allowed("anything")

    def test_deny_takes_precedence(self) -> None:
        policy = AgentPermissionPolicy(
            allowed_tools=["*"],
            denied_tools=["run_shell"],
        )
        assert policy.is_tool_allowed("read_file")
        assert not policy.is_tool_allowed("run_shell")

    def test_path_allowed_write(self) -> None:
        policy = AgentPermissionPolicy(allowed_paths=["workspace/*"])
        assert policy.is_path_allowed("workspace/file.txt", write=True)
        assert not policy.is_path_allowed("/etc/passwd", write=True)

    def test_path_read_only(self) -> None:
        policy = AgentPermissionPolicy(
            allowed_paths=[],
            read_only_paths=["docs/*"],
        )
        assert policy.is_path_allowed("docs/readme.md", write=False)
        assert not policy.is_path_allowed("docs/readme.md", write=True)

    def test_to_dict_from_dict_roundtrip(self) -> None:
        original = AgentPermissionPolicy(
            allowed_tools=["a", "b"],
            denied_tools=["c"],
            allowed_paths=["x/*"],
            read_only_paths=["y/*"],
            network_access=True,
            shell_access=True,
            max_file_size_bytes=5000,
        )
        data = original.to_dict()
        restored = AgentPermissionPolicy.from_dict(data)
        assert restored.allowed_tools == original.allowed_tools
        assert restored.denied_tools == original.denied_tools
        assert restored.network_access == original.network_access
        assert restored.max_file_size_bytes == 5000

    def test_allow_all_factory(self) -> None:
        policy = AgentPermissionPolicy.allow_all()
        assert policy.is_tool_allowed("anything")
        assert policy.network_access
        assert policy.shell_access
        assert policy.is_path_allowed("any/path", write=True)

    def test_max_file_size_default(self) -> None:
        policy = AgentPermissionPolicy()
        assert policy.max_file_size_bytes == 10 * 1024 * 1024
