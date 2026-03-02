"""Phase 11 — Team definition (domain).

Describes a team of agents: members, lead, workflow and
project context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


@dataclass
class AgentDescriptor:
    """Lightweight description of an agent within a team definition."""

    role: str
    name: str = ""
    model_backend: str = "ollama"
    model_name: str = ""
    persona_prompt: str = ""
    model_preference: str = ""
    skills: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "model_backend": self.model_backend,
            "model_name": self.model_name,
            "persona_prompt": self.persona_prompt,
            "model_preference": self.model_preference,
            "skills": self.skills,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentDescriptor":
        return cls(
            role=d.get("role", ""),
            name=d.get("name", ""),
            model_backend=d.get("model_backend", "ollama"),
            model_name=d.get("model_name", ""),
            persona_prompt=d.get("persona_prompt", ""),
            model_preference=d.get("model_preference", ""),
            skills=d.get("skills", []),
            metadata=d.get("metadata", {}),
        )


@dataclass
class TeamDefinition:
    """Describes a managed team of agents."""

    team_id: str
    name: str = ""
    members: list[AgentDescriptor] = field(default_factory=list)
    lead_agent_id: str = ""
    workflow: str = ""
    project_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- member management ----

    def add_member(self, member: AgentDescriptor) -> None:
        self.members.append(member)

    def remove_member(self, role: str) -> bool:
        before = len(self.members)
        self.members = [m for m in self.members if m.role != role]
        return len(self.members) < before

    def get_member(self, role: str) -> AgentDescriptor | None:
        for m in self.members:
            if m.role == role:
                return m
        return None

    @property
    def size(self) -> int:
        return len(self.members)

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "members": [m.to_dict() for m in self.members],
            "lead_agent_id": self.lead_agent_id,
            "workflow": self.workflow,
            "project_context": self.project_context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TeamDefinition":
        return cls(
            team_id=d.get("team_id", ""),
            name=d.get("name", ""),
            members=[AgentDescriptor.from_dict(m) for m in d.get("members", [])],
            lead_agent_id=d.get("lead_agent_id", ""),
            workflow=d.get("workflow", ""),
            project_context=d.get("project_context", ""),
            metadata=d.get("metadata", {}),
        )

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: Path) -> "TeamDefinition":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    def save_yaml(self, path: Path) -> None:
        """Save team definition to a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    @classmethod
    def load_yaml(cls, path: Path) -> "TeamDefinition":
        """Load team definition from a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)
