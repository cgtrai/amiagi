"""WorkflowDefinition — DAG-based workflow model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class NodeType(str, Enum):
    """Built-in workflow node types."""

    EXECUTE = "execute"
    REVIEW = "review"
    GATE = "gate"  # human approval required
    FAN_OUT = "fan_out"
    FAN_IN = "fan_in"
    CONDITIONAL = "conditional"


class NodeStatus(str, Enum):
    """Execution status for a single workflow node."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"


@dataclass
class WorkflowNode:
    """A single step in the workflow DAG."""

    node_id: str
    node_type: NodeType
    label: str = ""
    description: str = ""
    agent_role: str = ""  # required role (or "any")
    depends_on: list[str] = field(default_factory=list)
    condition: str = ""  # expression for CONDITIONAL nodes
    config: dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
            "description": self.description,
            "agent_role": self.agent_role,
            "depends_on": self.depends_on,
            "condition": self.condition,
            "config": self.config,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "WorkflowNode":
        return WorkflowNode(
            node_id=data["node_id"],
            node_type=NodeType(data.get("node_type", "execute")),
            label=data.get("label", ""),
            description=data.get("description", ""),
            agent_role=data.get("agent_role", ""),
            depends_on=data.get("depends_on", []),
            condition=data.get("condition", ""),
            config=data.get("config", {}),
        )


@dataclass
class WorkflowDefinition:
    """A complete workflow as a DAG of nodes."""

    name: str
    description: str = ""
    nodes: list[WorkflowNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def node_map(self) -> dict[str, WorkflowNode]:
        return {n.node_id: n for n in self.nodes}

    def roots(self) -> list[WorkflowNode]:
        """Nodes with no dependencies — workflow entry points."""
        return [n for n in self.nodes if not n.depends_on]

    def successors(self, node_id: str) -> list[WorkflowNode]:
        """Nodes that depend on *node_id*."""
        return [n for n in self.nodes if node_id in n.depends_on]

    def validate(self) -> list[str]:
        """Return validation errors (empty = valid)."""
        errors: list[str] = []
        ids = {n.node_id for n in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in ids:
                    errors.append(f"Node '{node.node_id}' depends on unknown '{dep}'")
        if not self.nodes:
            errors.append("Workflow has no nodes")
        if not self.roots():
            errors.append("Workflow has no root nodes (cycle detected?)")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "WorkflowDefinition":
        nodes = [WorkflowNode.from_dict(nd) for nd in data.get("nodes", [])]
        return WorkflowDefinition(
            name=data["name"],
            description=data.get("description", ""),
            nodes=nodes,
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def load_json(path: Path) -> "WorkflowDefinition":
        """Load a workflow definition from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return WorkflowDefinition.from_dict(data)

    def save_json(self, path: Path) -> None:
        """Save workflow definition to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def load_yaml(path: Path) -> "WorkflowDefinition":
        """Load a workflow definition from a YAML file."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return WorkflowDefinition.from_dict(data)

    def save_yaml(self, path: Path) -> None:
        """Save workflow definition to a YAML file."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    @staticmethod
    def load_file(path: Path) -> "WorkflowDefinition":
        """Load from JSON or YAML based on file extension."""
        if path.suffix in (".yaml", ".yml"):
            return WorkflowDefinition.load_yaml(path)
        return WorkflowDefinition.load_json(path)
