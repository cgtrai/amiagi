"""Phase 11 — Skill catalog (application).

Centralised registry of known agent skills with metadata for
automatic matching.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillEntry:
    """Describes a single agent skill."""

    name: str
    description: str = ""
    required_tools: list[str] = field(default_factory=list)
    min_context_tokens: int = 0
    compatible_models: list[str] = field(default_factory=list)
    difficulty_level: str = "medium"  # easy / medium / hard
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_tools": self.required_tools,
            "min_context_tokens": self.min_context_tokens,
            "compatible_models": self.compatible_models,
            "difficulty_level": self.difficulty_level,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillEntry":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            required_tools=d.get("required_tools", []),
            min_context_tokens=d.get("min_context_tokens", 0),
            compatible_models=d.get("compatible_models", []),
            difficulty_level=d.get("difficulty_level", "medium"),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )


class SkillCatalog:
    """Thread-safe central skill registry."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}
        self._lock = threading.Lock()

    # ---- CRUD ----

    def register(self, skill: SkillEntry) -> None:
        with self._lock:
            self._skills[skill.name] = skill

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._skills.pop(name, None) is not None

    def get(self, name: str) -> SkillEntry | None:
        with self._lock:
            return self._skills.get(name)

    def list_skills(self) -> list[SkillEntry]:
        with self._lock:
            return list(self._skills.values())

    # ---- search / match ----

    def search(self, query: str) -> list[SkillEntry]:
        """Case-insensitive search across name, description and tags."""
        q = query.lower()
        with self._lock:
            return [
                s for s in self._skills.values()
                if q in s.name.lower()
                or q in s.description.lower()
                or any(q in t.lower() for t in s.tags)
            ]

    def match_for_tools(self, available_tools: list[str]) -> list[SkillEntry]:
        """Return skills whose required_tools are all in *available_tools*."""
        tool_set = set(available_tools)
        with self._lock:
            return [
                s for s in self._skills.values()
                if set(s.required_tools).issubset(tool_set)
            ]

    def match_for_model(self, model_name: str) -> list[SkillEntry]:
        with self._lock:
            return [
                s for s in self._skills.values()
                if not s.compatible_models or model_name in s.compatible_models
            ]

    # ---- bulk operations ----

    def load_json(self, path: Path) -> int:
        """Load skills from a JSON file (array of skill dicts)."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        count = 0
        for item in raw:
            skill = SkillEntry.from_dict(item)
            self.register(skill)
            count += 1
        return count

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = [s.to_dict() for s in self._skills.values()]
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._skills)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "skills": [s.to_dict() for s in self._skills.values()],
                "count": len(self._skills),
            }
