"""Tests for WorkflowDefinition & WorkflowNode (Phase 6 — domain)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.domain.workflow import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)


class TestWorkflowNode:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        node = WorkflowNode(
            node_id="n1",
            node_type=NodeType.EXECUTE,
            label="Do stuff",
            description="Does stuff",
            agent_role="executor",
            depends_on=["n0"],
            condition="",
            config={"key": "val"},
        )
        data = node.to_dict()
        restored = WorkflowNode.from_dict(data)
        assert restored.node_id == "n1"
        assert restored.node_type == NodeType.EXECUTE
        assert restored.depends_on == ["n0"]
        assert restored.config == {"key": "val"}

    def test_default_status_pending(self) -> None:
        node = WorkflowNode(node_id="x", node_type=NodeType.GATE)
        assert node.status == NodeStatus.PENDING

    def test_from_dict_defaults(self) -> None:
        node = WorkflowNode.from_dict({"node_id": "x"})
        assert node.node_type == NodeType.EXECUTE
        assert node.label == ""
        assert node.depends_on == []


class TestWorkflowDefinition:
    def _simple_workflow(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            name="test",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="b", node_type=NodeType.REVIEW, depends_on=["a"]),
            ],
        )

    def test_node_map(self) -> None:
        wf = self._simple_workflow()
        nmap = wf.node_map()
        assert "a" in nmap
        assert "b" in nmap

    def test_roots(self) -> None:
        wf = self._simple_workflow()
        roots = wf.roots()
        assert len(roots) == 1
        assert roots[0].node_id == "a"

    def test_successors(self) -> None:
        wf = self._simple_workflow()
        succ = wf.successors("a")
        assert len(succ) == 1
        assert succ[0].node_id == "b"

    def test_validate_ok(self) -> None:
        wf = self._simple_workflow()
        assert wf.validate() == []

    def test_validate_missing_dep(self) -> None:
        wf = WorkflowDefinition(
            name="bad",
            nodes=[WorkflowNode(node_id="a", node_type=NodeType.EXECUTE, depends_on=["missing"])],
        )
        errors = wf.validate()
        assert any("missing" in e for e in errors)

    def test_validate_no_nodes(self) -> None:
        wf = WorkflowDefinition(name="empty", nodes=[])
        errors = wf.validate()
        assert any("no nodes" in e.lower() for e in errors)

    def test_to_dict_from_dict_roundtrip(self) -> None:
        wf = self._simple_workflow()
        wf.metadata = {"version": 1}
        data = wf.to_dict()
        restored = WorkflowDefinition.from_dict(data)
        assert restored.name == "test"
        assert len(restored.nodes) == 2
        assert restored.metadata == {"version": 1}

    def test_save_and_load_json(self, tmp_path: Path) -> None:
        wf = self._simple_workflow()
        path = tmp_path / "wf.json"
        wf.save_json(path)
        loaded = WorkflowDefinition.load_json(path)
        assert loaded.name == wf.name
        assert len(loaded.nodes) == len(wf.nodes)

    def test_node_types_enum(self) -> None:
        assert NodeType.EXECUTE.value == "execute"
        assert NodeType.GATE.value == "gate"
        assert NodeType.FAN_OUT.value == "fan_out"

    def test_node_status_enum(self) -> None:
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.WAITING_APPROVAL.value == "waiting_approval"
