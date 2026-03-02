"""Tests for WorkflowEngine (Phase 6)."""

from __future__ import annotations

import pytest

from amiagi.domain.workflow import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)
from amiagi.application.workflow_engine import WorkflowEngine, WorkflowRun


def _linear_workflow() -> WorkflowDefinition:
    """A → B → C (all EXECUTE)."""
    return WorkflowDefinition(
        name="linear",
        nodes=[
            WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
            WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["a"]),
            WorkflowNode(node_id="c", node_type=NodeType.EXECUTE, depends_on=["b"]),
        ],
    )


def _gate_workflow() -> WorkflowDefinition:
    """A → GATE → B."""
    return WorkflowDefinition(
        name="gated",
        nodes=[
            WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
            WorkflowNode(node_id="gate", node_type=NodeType.GATE, depends_on=["a"]),
            WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["gate"]),
        ],
    )


def _fan_out_in_workflow() -> WorkflowDefinition:
    """FAN_OUT → (B, C) → FAN_IN → D."""
    return WorkflowDefinition(
        name="parallel",
        nodes=[
            WorkflowNode(node_id="fan_out", node_type=NodeType.FAN_OUT),
            WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["fan_out"]),
            WorkflowNode(node_id="c", node_type=NodeType.EXECUTE, depends_on=["fan_out"]),
            WorkflowNode(node_id="fan_in", node_type=NodeType.FAN_IN, depends_on=["b", "c"]),
            WorkflowNode(node_id="d", node_type=NodeType.EXECUTE, depends_on=["fan_in"]),
        ],
    )


def _conditional_workflow() -> WorkflowDefinition:
    """A → CONDITIONAL (passes) → B."""
    return WorkflowDefinition(
        name="conditional",
        nodes=[
            WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
            WorkflowNode(
                node_id="cond",
                node_type=NodeType.CONDITIONAL,
                depends_on=["a"],
                condition="a.status == completed",
            ),
            WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["cond"]),
        ],
    )


class TestWorkflowEngine:
    def test_linear_completes(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_linear_workflow(), run_id="r1")
        assert run.status == "completed"
        assert all(n.status == NodeStatus.COMPLETED for n in run.workflow.nodes)

    def test_custom_executor(self) -> None:
        results: list[str] = []

        def executor(node: WorkflowNode, run: WorkflowRun) -> str:
            results.append(node.node_id)
            return f"done:{node.node_id}"

        engine = WorkflowEngine(executor=executor)
        run = engine.start(_linear_workflow(), run_id="r1")
        assert results == ["a", "b", "c"]
        assert run.status == "completed"

    def test_gate_blocks_until_approved(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_gate_workflow(), run_id="r1")
        # Gate should block — b should not be completed
        gate_node = run.workflow.node_map()["gate"]
        assert gate_node.status == NodeStatus.WAITING_APPROVAL
        b_node = run.workflow.node_map()["b"]
        assert b_node.status == NodeStatus.PENDING
        assert run.status == "running"

    def test_gate_approve_advances(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_gate_workflow(), run_id="r1")
        ok = engine.approve_gate("r1", "gate")
        assert ok
        assert run.status == "completed"

    def test_gate_approve_wrong_node(self) -> None:
        engine = WorkflowEngine()
        engine.start(_gate_workflow(), run_id="r1")
        assert not engine.approve_gate("r1", "a")  # 'a' is EXECUTE, not GATE

    def test_gate_approve_nonexistent_run(self) -> None:
        engine = WorkflowEngine()
        assert not engine.approve_gate("nope", "gate")

    def test_auto_gate_handler(self) -> None:
        engine = WorkflowEngine(gate_handler=lambda _node: True)
        run = engine.start(_gate_workflow(), run_id="r1")
        assert run.status == "completed"
        gate = run.workflow.node_map()["gate"]
        assert gate.result == "auto-approved"

    def test_fan_out_in(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_fan_out_in_workflow(), run_id="r1")
        assert run.status == "completed"
        for n in run.workflow.nodes:
            assert n.status == NodeStatus.COMPLETED

    def test_conditional_passes(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_conditional_workflow(), run_id="r1")
        assert run.status == "completed"
        cond = run.workflow.node_map()["cond"]
        assert cond.status == NodeStatus.COMPLETED  # condition was True

    def test_conditional_skips(self) -> None:
        wf = WorkflowDefinition(
            name="skip",
            nodes=[
                WorkflowNode(node_id="a", node_type=NodeType.EXECUTE),
                WorkflowNode(
                    node_id="cond",
                    node_type=NodeType.CONDITIONAL,
                    depends_on=["a"],
                    condition="a.status == failed",  # a completes, so this is False
                ),
                WorkflowNode(node_id="b", node_type=NodeType.EXECUTE, depends_on=["cond"]),
            ],
        )
        engine = WorkflowEngine()
        run = engine.start(wf, run_id="r1")
        cond = run.workflow.node_map()["cond"]
        assert cond.status == NodeStatus.SKIPPED

    def test_pause_and_resume(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_gate_workflow(), run_id="r1")
        assert engine.pause("r1")
        assert run.status == "paused"
        assert engine.resume("r1")
        assert run.status == "running"

    def test_pause_terminal_run(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_linear_workflow(), run_id="r1")
        assert run.status == "completed"
        assert not engine.pause("r1")

    def test_resume_non_paused(self) -> None:
        engine = WorkflowEngine()
        engine.start(_gate_workflow(), run_id="r1")
        assert not engine.resume("r1")  # not paused

    def test_list_runs(self) -> None:
        engine = WorkflowEngine()
        engine.start(_linear_workflow(), run_id="r1")
        engine.start(_linear_workflow(), run_id="r2")
        assert len(engine.list_runs()) == 2

    def test_get_run(self) -> None:
        engine = WorkflowEngine()
        engine.start(_linear_workflow(), run_id="r1")
        assert engine.get_run("r1") is not None
        assert engine.get_run("nope") is None

    def test_invalid_workflow_raises(self) -> None:
        engine = WorkflowEngine()
        wf = WorkflowDefinition(name="empty", nodes=[])
        with pytest.raises(ValueError, match="Invalid"):
            engine.start(wf)

    def test_node_failure_marks_run_failed(self) -> None:
        def failing_executor(node: WorkflowNode, run: WorkflowRun) -> str:
            if node.node_id == "b":
                raise RuntimeError("boom")
            return "ok"

        engine = WorkflowEngine(executor=failing_executor)
        run = engine.start(_linear_workflow(), run_id="r1")
        assert run.status == "failed"
        assert run.workflow.node_map()["b"].status == NodeStatus.FAILED

    def test_run_id_auto_generated(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_linear_workflow())
        assert run.run_id.startswith("run-")

    def test_is_terminal_property(self) -> None:
        engine = WorkflowEngine()
        run = engine.start(_linear_workflow(), run_id="r")
        assert run.is_terminal
