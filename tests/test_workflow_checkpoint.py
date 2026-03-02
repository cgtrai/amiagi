"""Tests for WorkflowCheckpoint (Phase 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.domain.workflow import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)
from amiagi.application.workflow_engine import WorkflowEngine, WorkflowRun
from amiagi.infrastructure.workflow_checkpoint import WorkflowCheckpoint


@pytest.fixture()
def checkpoint(tmp_path: Path) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(checkpoint_dir=tmp_path / "checkpoints")


def _sample_run() -> WorkflowRun:
    wf = WorkflowDefinition(
        name="test",
        nodes=[
            WorkflowNode(node_id="a", node_type=NodeType.EXECUTE, status=NodeStatus.COMPLETED, result="ok"),
            WorkflowNode(node_id="b", node_type=NodeType.GATE, depends_on=["a"], status=NodeStatus.WAITING_APPROVAL),
        ],
    )
    return WorkflowRun(workflow=wf, run_id="run-42", status="running")


class TestWorkflowCheckpoint:
    def test_save_and_load(self, checkpoint: WorkflowCheckpoint) -> None:
        run = _sample_run()
        path = checkpoint.save(run)
        assert path.exists()
        loaded = checkpoint.load("run-42")
        assert loaded is not None
        assert loaded.run_id == "run-42"
        assert loaded.status == "running"
        nmap = loaded.workflow.node_map()
        assert nmap["a"].status == NodeStatus.COMPLETED
        assert nmap["a"].result == "ok"
        assert nmap["b"].status == NodeStatus.WAITING_APPROVAL

    def test_load_nonexistent(self, checkpoint: WorkflowCheckpoint) -> None:
        assert checkpoint.load("nope") is None

    def test_list_checkpoints(self, checkpoint: WorkflowCheckpoint) -> None:
        checkpoint.save(_sample_run())
        run2 = _sample_run()
        run2.run_id = "run-99"
        checkpoint.save(run2)
        listing = checkpoint.list_checkpoints()
        assert "run-42" in listing
        assert "run-99" in listing

    def test_delete(self, checkpoint: WorkflowCheckpoint) -> None:
        checkpoint.save(_sample_run())
        assert checkpoint.delete("run-42")
        assert checkpoint.load("run-42") is None

    def test_delete_nonexistent(self, checkpoint: WorkflowCheckpoint) -> None:
        assert not checkpoint.delete("nope")

    def test_save_overwrites(self, checkpoint: WorkflowCheckpoint) -> None:
        run = _sample_run()
        checkpoint.save(run)
        run.status = "completed"
        checkpoint.save(run)
        loaded = checkpoint.load("run-42")
        assert loaded is not None
        assert loaded.status == "completed"

    def test_workflow_data_preserved(self, checkpoint: WorkflowCheckpoint) -> None:
        run = _sample_run()
        checkpoint.save(run)
        loaded = checkpoint.load("run-42")
        assert loaded is not None
        assert loaded.workflow.name == "test"
        assert len(loaded.workflow.nodes) == 2

    def test_checkpoint_with_engine_run(self, checkpoint: WorkflowCheckpoint) -> None:
        """Integration: save a real engine run then restore it."""
        wf = WorkflowDefinition(
            name="full",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
            ],
        )
        engine = WorkflowEngine()
        run = engine.start(wf, run_id="real-run")
        checkpoint.save(run)
        loaded = checkpoint.load("real-run")
        assert loaded is not None
        assert loaded.workflow.node_map()["a"].status == NodeStatus.COMPLETED
        assert loaded.workflow.node_map()["gate"].status == NodeStatus.WAITING_APPROVAL
