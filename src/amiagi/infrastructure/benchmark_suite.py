"""BenchmarkSuite — loads predefined task sets from benchmarks/ directories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amiagi.application.eval_runner import EvalScenario


@dataclass
class BenchmarkCategory:
    """A named category of benchmark scenarios."""

    name: str
    description: str = ""
    scenarios: list[EvalScenario] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "prompt": s.prompt,
                    "expected_keywords": s.expected_keywords,
                    "category": s.category,
                    "metadata": s.metadata,
                }
                for s in self.scenarios
            ],
        }


class BenchmarkSuite:
    """Manages predefined benchmark task sets.

    Loaded from JSON files in ``benchmarks/<category>/*.json``.

    Usage::

        suite = BenchmarkSuite(benchmarks_dir=Path("./benchmarks"))
        suite.load_all()
        scenarios = suite.get_scenarios("code_review")
    """

    def __init__(self, benchmarks_dir: Path) -> None:
        self._dir = benchmarks_dir
        self._categories: dict[str, BenchmarkCategory] = {}

    @property
    def benchmarks_dir(self) -> Path:
        return self._dir

    def load_all(self) -> int:
        """Scan and load all benchmark JSON files. Returns count loaded."""
        if not self._dir.exists():
            return 0

        count = 0
        for cat_dir in sorted(self._dir.iterdir()):
            if cat_dir.is_dir():
                cat = BenchmarkCategory(name=cat_dir.name)
                for json_file in sorted(cat_dir.glob("*.json")):
                    scenarios = self._load_file(json_file, cat.name)
                    cat.scenarios.extend(scenarios)
                    count += len(scenarios)
                if cat.scenarios:
                    self._categories[cat.name] = cat
            elif cat_dir.suffix == ".json":
                # Top-level benchmark file
                cat_name = cat_dir.stem
                scenarios = self._load_file(cat_dir, cat_name)
                if scenarios:
                    cat = self._categories.get(cat_name, BenchmarkCategory(name=cat_name))
                    cat.scenarios.extend(scenarios)
                    self._categories[cat_name] = cat
                    count += len(scenarios)

        return count

    def add_category(self, category: BenchmarkCategory) -> None:
        """Add or replace a category programmatically."""
        self._categories[category.name] = category

    def list_categories(self) -> list[str]:
        return sorted(self._categories.keys())

    def get_category(self, name: str) -> BenchmarkCategory | None:
        return self._categories.get(name)

    def get_scenarios(self, category: str) -> list[EvalScenario]:
        cat = self._categories.get(category)
        if cat is None:
            return []
        return list(cat.scenarios)

    def all_scenarios(self) -> list[EvalScenario]:
        result: list[EvalScenario] = []
        for cat in self._categories.values():
            result.extend(cat.scenarios)
        return result

    def total_count(self) -> int:
        return sum(len(c.scenarios) for c in self._categories.values())

    # ---- internals ----

    def _load_file(self, path: Path, category: str) -> list[EvalScenario]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        scenarios: list[EvalScenario] = []
        items = data if isinstance(data, list) else data.get("scenarios", [])
        for item in items:
            scenarios.append(
                EvalScenario(
                    scenario_id=item.get("scenario_id", item.get("id", path.stem)),
                    prompt=item.get("prompt", ""),
                    expected_keywords=item.get("expected_keywords", []),
                    category=category,
                    metadata=item.get("metadata", {}),
                )
            )
        return scenarios
