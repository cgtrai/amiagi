"""Tests for SharedWorkspace (Phase 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.infrastructure.shared_workspace import FileChange, SharedWorkspace


@pytest.fixture()
def workspace(tmp_path: Path) -> SharedWorkspace:
    return SharedWorkspace(root=tmp_path / "ws")


class TestSharedWorkspace:
    def test_write_and_read(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("hello.txt", "world", agent_id="a1")
        assert workspace.read_file("hello.txt") == "world"

    def test_read_nonexistent(self, workspace: SharedWorkspace) -> None:
        assert workspace.read_file("nope.txt") is None

    def test_append_file(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("log.txt", "line1\n", agent_id="a1")
        workspace.append_file("log.txt", "line2\n", agent_id="a2")
        content = workspace.read_file("log.txt")
        assert content is not None
        assert "line1" in content
        assert "line2" in content

    def test_delete_file(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("temp.txt", "x", agent_id="a1")
        assert workspace.delete_file("temp.txt", agent_id="a1")
        assert workspace.read_file("temp.txt") is None

    def test_delete_nonexistent(self, workspace: SharedWorkspace) -> None:
        assert not workspace.delete_file("nope.txt", agent_id="a1")

    def test_list_files(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("a.txt", "a", agent_id="a1")
        workspace.write_file("sub/b.txt", "b", agent_id="a2")
        files = workspace.list_files()
        assert "a.txt" in files
        assert "sub/b.txt" in files

    def test_changes_log(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("f.txt", "data", agent_id="a1")
        workspace.append_file("f.txt", "more", agent_id="a2")
        all_changes = workspace.changes()
        assert len(all_changes) == 2
        a1_changes = workspace.changes(agent_id="a1")
        assert len(a1_changes) == 1
        assert a1_changes[0].action == "write"

    def test_last_author(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("f.txt", "v1", agent_id="a1")
        workspace.write_file("f.txt", "v2", agent_id="a2")
        assert workspace.last_author("f.txt") == "a2"

    def test_last_author_unknown(self, workspace: SharedWorkspace) -> None:
        assert workspace.last_author("no_file.txt") is None

    def test_path_traversal_rejected(self, workspace: SharedWorkspace) -> None:
        with pytest.raises(ValueError, match="traversal"):
            workspace.write_file("../../etc/passwd", "x", agent_id="evil")

    def test_jsonl_log_written(self, workspace: SharedWorkspace) -> None:
        workspace.write_file("f.txt", "hi", agent_id="a1")
        log_path = workspace.root / ".workspace_log.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        assert entry["agent_id"] == "a1"
        assert entry["action"] == "write"

    def test_root_property(self, workspace: SharedWorkspace) -> None:
        assert workspace.root.exists()
