"""EvalRubric — configurable evaluation criteria with weighted scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


@dataclass
class Criterion:
    """A single evaluation criterion."""

    name: str
    description: str = ""
    weight: float = 1.0
    max_score: float = 5.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "max_score": self.max_score,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Criterion":
        return Criterion(
            name=data["name"],
            description=data.get("description", ""),
            weight=float(data.get("weight", 1.0)),
            max_score=float(data.get("max_score", 5.0)),
        )


@dataclass
class EvalResult:
    """Scores for a single evaluation run."""

    scores: dict[str, float] = field(default_factory=dict)  # criterion_name -> score
    notes: dict[str, str] = field(default_factory=dict)  # criterion_name -> note
    aggregate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": self.scores,
            "notes": self.notes,
            "aggregate": self.aggregate,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "EvalResult":
        return EvalResult(
            scores=data.get("scores", {}),
            notes=data.get("notes", {}),
            aggregate=float(data.get("aggregate", 0.0)),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EvalRubric:
    """A set of weighted evaluation criteria.

    Usage::

        rubric = EvalRubric(name="code_review")
        rubric.add_criterion(Criterion("correctness", weight=2.0))
        rubric.add_criterion(Criterion("style", weight=1.0))
        result = rubric.score({"correctness": 4.0, "style": 3.0})
    """

    name: str = "default"
    criteria: list[Criterion] = field(default_factory=list)
    description: str = ""

    def add_criterion(self, criterion: Criterion) -> None:
        self.criteria.append(criterion)

    def remove_criterion(self, name: str) -> None:
        self.criteria = [c for c in self.criteria if c.name != name]

    def get_criterion(self, name: str) -> Criterion | None:
        for c in self.criteria:
            if c.name == name:
                return c
        return None

    def criterion_names(self) -> list[str]:
        return [c.name for c in self.criteria]

    def score(
        self,
        raw_scores: dict[str, float],
        notes: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvalResult:
        """Compute weighted aggregate from *raw_scores*."""
        total_weight = sum(c.weight for c in self.criteria)
        if total_weight == 0:
            return EvalResult(
                scores=raw_scores,
                notes=notes or {},
                aggregate=0.0,
                metadata=metadata or {},
            )

        weighted_sum = 0.0
        for c in self.criteria:
            raw = raw_scores.get(c.name, 0.0)
            clamped = max(0.0, min(raw, c.max_score))
            normalised = clamped / c.max_score if c.max_score > 0 else 0.0
            weighted_sum += normalised * c.weight

        aggregate = round((weighted_sum / total_weight) * 100, 2)

        return EvalResult(
            scores=raw_scores,
            notes=notes or {},
            aggregate=aggregate,
            metadata=metadata or {},
        )

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "criteria": [c.to_dict() for c in self.criteria],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "EvalRubric":
        return EvalRubric(
            name=data.get("name", "default"),
            description=data.get("description", ""),
            criteria=[Criterion.from_dict(c) for c in data.get("criteria", [])],
        )

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def load_json(path: Path) -> "EvalRubric":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return EvalRubric.from_dict(raw)

    # ---- YAML persistence ----

    def save_yaml(self, path: Path) -> None:
        """Save rubric to a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required for YAML support: pip install pyyaml")
        assert yaml is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    @staticmethod
    def load_yaml(path: Path) -> "EvalRubric":
        """Load rubric from a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required for YAML support: pip install pyyaml")
        assert yaml is not None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return EvalRubric.from_dict(raw)

    # ---- factory for default criteria ----

    @staticmethod
    def default() -> "EvalRubric":
        """Return a rubric pre-loaded with standard criteria."""
        rubric = EvalRubric(name="default", description="Standard evaluation rubric")
        rubric.add_criterion(Criterion("correctness", "Poprawność odpowiedzi", weight=2.0))
        rubric.add_criterion(Criterion("completeness", "Kompletność odpowiedzi", weight=1.5))
        rubric.add_criterion(Criterion("style", "Styl i czytelność", weight=1.0))
        rubric.add_criterion(Criterion("tool_efficiency", "Efektywność użycia narzędzi", weight=1.0))
        return rubric
