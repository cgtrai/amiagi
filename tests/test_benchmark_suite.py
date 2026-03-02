"""Tests for BenchmarkSuite."""

from __future__ import annotations

import json
from pathlib import Path

from amiagi.application.eval_runner import EvalScenario
from amiagi.infrastructure.benchmark_suite import BenchmarkCategory, BenchmarkSuite


class TestBenchmarkSuite:
    def test_load_empty_dir(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(tmp_path / "benchmarks")
        count = suite.load_all()
        assert count == 0

    def test_load_category_dir(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks" / "code_review"
        bdir.mkdir(parents=True)
        data = [
            {"scenario_id": "cr1", "prompt": "Review this code", "expected_keywords": ["bug"]},
            {"scenario_id": "cr2", "prompt": "Check style"},
        ]
        (bdir / "basic.json").write_text(json.dumps(data), encoding="utf-8")

        suite = BenchmarkSuite(tmp_path / "benchmarks")
        count = suite.load_all()
        assert count == 2
        assert "code_review" in suite.list_categories()

    def test_load_top_level_json(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks"
        bdir.mkdir()
        data = {"scenarios": [{"scenario_id": "t1", "prompt": "hello"}]}
        (bdir / "general.json").write_text(json.dumps(data), encoding="utf-8")

        suite = BenchmarkSuite(bdir)
        count = suite.load_all()
        assert count == 1
        assert "general" in suite.list_categories()

    def test_get_scenarios(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(tmp_path)
        cat = BenchmarkCategory(
            name="test",
            scenarios=[
                EvalScenario(scenario_id="s1", prompt="q1"),
                EvalScenario(scenario_id="s2", prompt="q2"),
            ],
        )
        suite.add_category(cat)
        assert len(suite.get_scenarios("test")) == 2
        assert suite.get_scenarios("nope") == []

    def test_total_count(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(tmp_path)
        suite.add_category(BenchmarkCategory(
            name="a",
            scenarios=[EvalScenario(scenario_id="s1", prompt="q")],
        ))
        suite.add_category(BenchmarkCategory(
            name="b",
            scenarios=[
                EvalScenario(scenario_id="s2", prompt="q"),
                EvalScenario(scenario_id="s3", prompt="q"),
            ],
        ))
        assert suite.total_count() == 3

    def test_all_scenarios(self, tmp_path: Path) -> None:
        suite = BenchmarkSuite(tmp_path)
        suite.add_category(BenchmarkCategory(
            name="x", scenarios=[EvalScenario(scenario_id="s1", prompt="p")],
        ))
        assert len(suite.all_scenarios()) == 1

    def test_invalid_json_skipped(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks" / "bad"
        bdir.mkdir(parents=True)
        (bdir / "broken.json").write_text("{invalid json!!!", encoding="utf-8")
        suite = BenchmarkSuite(tmp_path / "benchmarks")
        count = suite.load_all()
        assert count == 0

    def test_category_to_dict(self) -> None:
        cat = BenchmarkCategory(
            name="cat1",
            scenarios=[EvalScenario(scenario_id="s1", prompt="hello", expected_keywords=["hi"])],
        )
        d = cat.to_dict()
        assert d["name"] == "cat1"
        assert len(d["scenarios"]) == 1

    def test_load_yaml_category(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks" / "planning"
        bdir.mkdir(parents=True)
        yaml_content = (
            "- scenario_id: p1\n"
            "  prompt: Create a project plan\n"
            "  expected_keywords:\n"
            "    - milestone\n"
            "    - deadline\n"
            "- scenario_id: p2\n"
            "  prompt: Estimate effort\n"
        )
        (bdir / "basic.yaml").write_text(yaml_content, encoding="utf-8")

        suite = BenchmarkSuite(tmp_path / "benchmarks")
        count = suite.load_all()
        assert count == 2
        assert "planning" in suite.list_categories()
        scenarios = suite.get_scenarios("planning")
        assert scenarios[0].scenario_id == "p1"
        assert "milestone" in scenarios[0].expected_keywords

    def test_load_yml_extension(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks" / "review"
        bdir.mkdir(parents=True)
        yml_content = (
            "scenarios:\n"
            "  - scenario_id: r1\n"
            "    prompt: Review this PR\n"
        )
        (bdir / "cases.yml").write_text(yml_content, encoding="utf-8")

        suite = BenchmarkSuite(tmp_path / "benchmarks")
        count = suite.load_all()
        assert count == 1
        assert "review" in suite.list_categories()

    def test_load_top_level_yaml(self, tmp_path: Path) -> None:
        bdir = tmp_path / "benchmarks"
        bdir.mkdir()
        yaml_content = (
            "scenarios:\n"
            "  - scenario_id: g1\n"
            "    prompt: General task\n"
        )
        (bdir / "general.yaml").write_text(yaml_content, encoding="utf-8")

        suite = BenchmarkSuite(bdir)
        count = suite.load_all()
        assert count == 1
        assert "general" in suite.list_categories()
