"""WorkflowCheckpoint — serialise / restore workflow run state."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from amiagi.domain.workflow import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)
from amiagi.application.workflow_engine import WorkflowRun


class WorkflowCheckpoint:
    """Persist and restore :class:`WorkflowRun` state to JSON files.

    Each run is saved as ``<checkpoint_dir>/<run_id>.json``.
    """

    def __init__(self, checkpoint_dir: Path) -> None:
        self._dir = checkpoint_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, run: WorkflowRun) -> Path:
        """Save run state and return the checkpoint file path."""
        data: dict[str, Any] = {
            "run_id": run.run_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "workflow": run.workflow.to_dict(),
            "node_states": {
                n.node_id: {
                    "status": n.status.value,
                    "result": n.result,
                }
                for n in run.workflow.nodes
            },
        }
        path = self._dir / f"{run.run_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, run_id: str) -> WorkflowRun | None:
        """Restore a run from checkpoint. Returns ``None`` if not found."""
        path = self._dir / f"{run_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))

        workflow = WorkflowDefinition.from_dict(data["workflow"])

        # Restore per-node status
        node_states = data.get("node_states", {})
        for node in workflow.nodes:
            ns = node_states.get(node.node_id, {})
            node.status = NodeStatus(ns.get("status", "pending"))
            node.result = ns.get("result", "")

        run = WorkflowRun(
            workflow=workflow,
            run_id=data["run_id"],
            started_at=data.get("started_at", time.time()),
            finished_at=data.get("finished_at"),
            status=data.get("status", "running"),
        )
        return run

    def list_checkpoints(self) -> list[str]:
        """Return run IDs that have saved checkpoints."""
        return [p.stem for p in sorted(self._dir.glob("*.json"))]

    def delete(self, run_id: str) -> bool:
        """Remove a checkpoint. Returns ``True`` if deleted."""
        path = self._dir / f"{run_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
