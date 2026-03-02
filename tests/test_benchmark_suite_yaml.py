"""Tests for BenchmarkSuite — loading YAML scenario files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from amiagi.infrastructure.benchmark_suite import BenchmarkSuite


def _create_yaml_benchmark(
    base: Path,
    category: str,
    filename: str,
    scenarios: list[dict[str, Any]],
) -> Path:
    d = base / category
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(
        yaml.dump({"scenarios": scenarios}, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return p


def test_load_yaml_benchmark(tmp_path: Path) -> None:
    _create_yaml_benchmark(tmp_path, "code_gen", "basic.yaml", [
        {
            "scenario_id": "cg1",
            "prompt": "Write hello world",
            "expected_keywords": ["print", "hello"],
        },
        {
            "scenario_id": "cg2",
            "prompt": "Sort a list",
            "expected_keywords": ["sort"],
        },
    ])

    suite = BenchmarkSuite(tmp_path)
    count = suite.load_all()
    assert count == 2
    assert "code_gen" in suite.list_categories()
    scenarios = suite.get_scenarios("code_gen")
    assert len(scenarios) == 2
    assert scenarios[0].scenario_id == "cg1"
    assert scenarios[0].category == "code_gen"


def test_load_multiple_categories(tmp_path: Path) -> None:
    _create_yaml_benchmark(tmp_path, "review", "sec.yaml", [
        {"scenario_id": "r1", "prompt": "Review eval usage", "expected_keywords": ["eval"]},
    ])
    _create_yaml_benchmark(tmp_path, "planning", "sprint.yaml", [
        {"scenario_id": "p1", "prompt": "Plan a sprint", "expected_keywords": ["sprint"]},
    ])

    suite = BenchmarkSuite(tmp_path)
    count = suite.load_all()
    assert count == 2
    assert set(suite.list_categories()) == {"review", "planning"}


def test_load_json_benchmark(tmp_path: Path) -> None:
    d = tmp_path / "mixed"
    d.mkdir()
    (d / "test.json").write_text(json.dumps([
        {"scenario_id": "j1", "prompt": "JSON test", "expected_keywords": ["json"]},
    ]), encoding="utf-8")

    suite = BenchmarkSuite(tmp_path)
    count = suite.load_all()
    assert count == 1
    assert suite.get_scenarios("mixed")[0].scenario_id == "j1"


def test_all_scenarios(tmp_path: Path) -> None:
    _create_yaml_benchmark(tmp_path, "a", "a.yaml", [
        {"scenario_id": "a1", "prompt": "A", "expected_keywords": []},
    ])
    _create_yaml_benchmark(tmp_path, "b", "b.yaml", [
        {"scenario_id": "b1", "prompt": "B", "expected_keywords": []},
        {"scenario_id": "b2", "prompt": "B2", "expected_keywords": []},
    ])

    suite = BenchmarkSuite(tmp_path)
    suite.load_all()
    assert suite.total_count() == 3
    assert len(suite.all_scenarios()) == 3


def test_empty_dir(tmp_path: Path) -> None:
    suite = BenchmarkSuite(tmp_path)
    count = suite.load_all()
    assert count == 0
    assert suite.list_categories() == []


def test_nonexistent_dir() -> None:
    suite = BenchmarkSuite(Path("/nonexistent/path"))
    count = suite.load_all()
    assert count == 0


def test_get_scenarios_unknown_category(tmp_path: Path) -> None:
    suite = BenchmarkSuite(tmp_path)
    suite.load_all()
    assert suite.get_scenarios("nonexistent") == []


def test_builtin_benchmarks_load() -> None:
    """Load the actual benchmarks/ directory from the project root."""
    benchmarks_dir = Path(__file__).parent.parent / "benchmarks"
    if not benchmarks_dir.exists():
        return  # skip if dir missing in CI
    suite = BenchmarkSuite(benchmarks_dir)
    count = suite.load_all()
    assert count >= 10  # we created 20 scenarios across 4 categories
    assert len(suite.list_categories()) >= 4
