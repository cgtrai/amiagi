"""WorkflowEngine — DAG interpreter that executes workflow definitions."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from amiagi.domain.workflow import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowRun:
    """Runtime state for a single workflow execution."""

    workflow: WorkflowDefinition
    run_id: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"  # "running" | "completed" | "failed" | "paused"

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed")


# Callback signature: (node, workflow_run) -> result string
NodeExecutor = Callable[[WorkflowNode, WorkflowRun], str]


class WorkflowEngine:
    """Interprets a :class:`WorkflowDefinition` DAG.

    Nodes are executed by a pluggable *executor* callback.  The engine
    handles dependency resolution, fan-out/fan-in, gate nodes and
    conditional branching.
    """

    def __init__(
        self,
        *,
        executor: NodeExecutor | None = None,
        gate_handler: Callable[[WorkflowNode], bool] | None = None,
    ) -> None:
        self._executor = executor or self._default_executor
        self._gate_handler = gate_handler  # returns True = approved
        self._lock = threading.Lock()
        self._runs: dict[str, WorkflowRun] = {}

    # ---- public API ----

    def start(self, workflow: WorkflowDefinition, run_id: str = "") -> WorkflowRun:
        """Create a new run and begin execution."""
        errors = workflow.validate()
        if errors:
            raise ValueError(f"Invalid workflow: {'; '.join(errors)}")

        run_id = run_id or f"run-{int(time.time())}"
        run = WorkflowRun(workflow=workflow, run_id=run_id)

        # Reset all node statuses
        for node in workflow.nodes:
            node.status = NodeStatus.PENDING
            node.result = ""

        with self._lock:
            self._runs[run_id] = run

        self._advance(run)
        return run

    def advance(self, run_id: str) -> WorkflowRun | None:
        """Manually advance a run (e.g. after gate approval)."""
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return None
        self._advance(run)
        return run

    def approve_gate(self, run_id: str, node_id: str) -> bool:
        """Approve a GATE node. Returns ``False`` if node not found or not a gate."""
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return False
        nmap = run.workflow.node_map()
        node = nmap.get(node_id)
        if node is None or node.node_type != NodeType.GATE:
            return False
        if node.status != NodeStatus.WAITING_APPROVAL:
            return False
        node.status = NodeStatus.COMPLETED
        node.result = "approved"
        self._advance(run)
        return True

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[WorkflowRun]:
        return list(self._runs.values())

    def pause(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.is_terminal:
            return False
        run.status = "paused"
        return True

    def resume(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.status != "paused":
            return False
        run.status = "running"
        self._advance(run)
        return True

    # ---- execution loop ----

    def _advance(self, run: WorkflowRun) -> None:
        """Process all ready nodes until none can proceed."""
        if run.status == "paused":
            return

        nmap = run.workflow.node_map()
        changed = True
        while changed:
            changed = False
            for node in run.workflow.nodes:
                if node.status != NodeStatus.PENDING:
                    continue
                if self._deps_satisfied(node, nmap):
                    changed = True
                    self._execute_node(node, run, nmap)

        # Mark unreachable nodes as SKIPPED (e.g. downstream of a FAILED node)
        self._skip_unreachable(run, nmap)

        # Check completion
        if all(
            n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.FAILED)
            for n in run.workflow.nodes
        ):
            has_failure = any(n.status == NodeStatus.FAILED for n in run.workflow.nodes)
            run.status = "failed" if has_failure else "completed"
            run.finished_at = time.time()

    def _deps_satisfied(
        self, node: WorkflowNode, nmap: dict[str, WorkflowNode]
    ) -> bool:
        """All dependencies must be COMPLETED or SKIPPED."""
        for dep_id in node.depends_on:
            dep = nmap.get(dep_id)
            if dep is None:
                return False
            if dep.status not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                return False
        return True

    def _skip_unreachable(
        self, run: WorkflowRun, nmap: dict[str, WorkflowNode]
    ) -> None:
        """Mark PENDING nodes as SKIPPED if any dependency FAILED."""
        changed = True
        while changed:
            changed = False
            for node in run.workflow.nodes:
                if node.status != NodeStatus.PENDING:
                    continue
                for dep_id in node.depends_on:
                    dep = nmap.get(dep_id)
                    if dep is not None and dep.status in (NodeStatus.FAILED, NodeStatus.SKIPPED):
                        # Check if dep actually failed (or was skipped due to failure)
                        if dep.status == NodeStatus.FAILED or self._has_failed_ancestor(dep, nmap):
                            node.status = NodeStatus.SKIPPED
                            node.result = f"skipped:dependency {dep_id} failed"
                            changed = True
                            break

    def _has_failed_ancestor(
        self, node: WorkflowNode, nmap: dict[str, WorkflowNode]
    ) -> bool:
        """Check if a node has a FAILED ancestor in its dependency chain."""
        visited: set[str] = set()
        stack = list(node.depends_on)
        while stack:
            dep_id = stack.pop()
            if dep_id in visited:
                continue
            visited.add(dep_id)
            dep = nmap.get(dep_id)
            if dep is None:
                continue
            if dep.status == NodeStatus.FAILED:
                return True
            stack.extend(dep.depends_on)
        return False

    def _execute_node(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        nmap: dict[str, WorkflowNode],
    ) -> None:
        """Execute a single node based on its type."""
        # CONDITIONAL — evaluate and skip if false
        if node.node_type == NodeType.CONDITIONAL:
            if not self._eval_condition(node, nmap):
                node.status = NodeStatus.SKIPPED
                return

        # GATE — wait for human approval
        if node.node_type == NodeType.GATE:
            if self._gate_handler is not None and self._gate_handler(node):
                node.status = NodeStatus.COMPLETED
                node.result = "auto-approved"
            else:
                node.status = NodeStatus.WAITING_APPROVAL
            return

        # FAN_IN — just mark complete (synchronisation point)
        if node.node_type == NodeType.FAN_IN:
            node.status = NodeStatus.COMPLETED
            node.result = "fan-in sync"
            return

        # FAN_OUT — mark complete (downstream nodes will be unblocked)
        if node.node_type == NodeType.FAN_OUT:
            node.status = NodeStatus.COMPLETED
            node.result = "fan-out"
            return

        # EXECUTE / REVIEW — delegate to executor
        node.status = NodeStatus.RUNNING
        try:
            result = self._executor(node, run)
            node.status = NodeStatus.COMPLETED
            node.result = result
        except Exception as exc:
            node.status = NodeStatus.FAILED
            node.result = str(exc)
            logger.error("Node %s failed: %s", node.node_id, exc)

    def _eval_condition(
        self, node: WorkflowNode, nmap: dict[str, WorkflowNode]
    ) -> bool:
        """Condition evaluation with support for status checks and result matching.

        Supported patterns:
        - ``node_id.status == completed``
        - ``node_id.status == failed``
        - ``node_id.result == 'some value'``
        - ``result == 'pass'`` (references first dependency)
        Default: ``True`` (pass through).
        """
        cond = node.condition.strip()
        if not cond:
            return True

        import re
        # Pattern: "node_id.status == value" or "node_id.result == 'value'"
        match = re.match(
            r"^(\w+)\.(status|result)\s*==\s*['\"]?([^'\"]+)['\"]?$",
            cond,
        )
        if match:
            ref_id, attr, expected = match.groups()
            ref_node = nmap.get(ref_id)
            if ref_node is not None:
                if attr == "status":
                    return ref_node.status.value == expected.strip()
                if attr == "result":
                    return ref_node.result.strip() == expected.strip()
            return False

        # Pattern: "result == 'value'" (reference first dependency)
        match = re.match(
            r"^result\s*==\s*['\"]?([^'\"]+)['\"]?$",
            cond,
        )
        if match and node.depends_on:
            expected = match.group(1).strip()
            dep_node = nmap.get(node.depends_on[0])
            if dep_node is not None:
                return dep_node.result.strip() == expected
            return False

        # Fallback: simple "completed"/"failed" keyword check
        parts = cond.split(".")
        if len(parts) >= 2:
            ref_id = parts[0]
            ref_node = nmap.get(ref_id)
            if ref_node is not None:
                if "completed" in cond:
                    return ref_node.status == NodeStatus.COMPLETED
                if "failed" in cond:
                    return ref_node.status == NodeStatus.FAILED
        return True

    @staticmethod
    def _default_executor(node: WorkflowNode, run: WorkflowRun) -> str:
        """Default no-op executor."""
        return f"executed:{node.node_id}"
