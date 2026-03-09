"""Tests for SandboxManager (Phase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.infrastructure.sandbox_manager import SandboxManager


@pytest.fixture()
def sandboxes(tmp_path: Path) -> SandboxManager:
    return SandboxManager(root=tmp_path / "sandboxes")


class TestSandboxManager:
    def test_create_and_get(self, sandboxes: SandboxManager) -> None:
        path = sandboxes.create("agent1")
        assert path.exists()
        assert sandboxes.get("agent1") == path

    def test_get_nonexistent(self, sandboxes: SandboxManager) -> None:
        assert sandboxes.get("nope") is None

    def test_destroy(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("agent1")
        assert sandboxes.destroy("agent1")
        assert sandboxes.get("agent1") is None

    def test_destroy_nonexistent(self, sandboxes: SandboxManager) -> None:
        assert not sandboxes.destroy("nope")

    def test_list_sandboxes(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        sandboxes.create("a2")
        listing = sandboxes.list_sandboxes()
        assert "a1" in listing
        assert "a2" in listing

    def test_resolve_path_valid(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        resolved = sandboxes.resolve_path("a1", "subdir/file.txt")
        assert resolved is not None
        assert "a1" in str(resolved)

    def test_resolve_path_traversal(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        result = sandboxes.resolve_path("a1", "../../etc/passwd")
        assert result is None

    def test_resolve_path_no_sandbox(self, sandboxes: SandboxManager) -> None:
        assert sandboxes.resolve_path("nope", "file.txt") is None

    def test_sandbox_size(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        sb = sandboxes.get("a1")
        assert sb is not None
        (sb / "test.txt").write_text("hello world", encoding="utf-8")
        size = sandboxes.sandbox_size("a1")
        assert size > 0

    def test_sandbox_size_empty(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        assert sandboxes.sandbox_size("a1") == 0

    def test_sandbox_size_no_sandbox(self, sandboxes: SandboxManager) -> None:
        assert sandboxes.sandbox_size("nope") == 0

    def test_list_files_returns_top_level_items(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        sb = sandboxes.get("a1")
        assert sb is not None
        (sb / "b.txt").write_text("b", encoding="utf-8")
        (sb / "folder").mkdir()

        items = sandboxes.list_files("a1")

        assert [item.name for item in items] == ["folder", "b.txt"]

    def test_list_files_missing_sandbox_returns_empty(self, sandboxes: SandboxManager) -> None:
        assert sandboxes.list_files("missing") == []

    def test_root_property(self, sandboxes: SandboxManager) -> None:
        assert sandboxes.root.exists()

    def test_destroy_removes_files(self, sandboxes: SandboxManager) -> None:
        sandboxes.create("a1")
        sb = sandboxes.get("a1")
        assert sb is not None
        (sb / "data.bin").write_bytes(b"\x00" * 100)
        sandboxes.destroy("a1")
        assert not sb.exists()
