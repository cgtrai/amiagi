"""Tests for RegressionDetector."""

from __future__ import annotations

from pathlib import Path

from amiagi.application.eval_runner import EvalRunResult
from amiagi.application.regression_detector import RegressionDetector


class TestRegressionDetector:
    def test_no_baseline_no_regression(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        current = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=80.0)
        report = d.check(current)
        assert report.regressed is False
        assert report.baseline_score == 0.0

    def test_save_and_load_baseline(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        result = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=85.0)
        d.save_baseline(result)
        loaded = d.load_baseline("a")
        assert loaded is not None
        assert loaded.aggregate_score == 85.0

    def test_regression_detected(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines", threshold=5.0)
        baseline = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=80.0)
        d.save_baseline(baseline)
        current = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=70.0)
        report = d.check(current)
        assert report.regressed is True
        assert report.delta == -10.0

    def test_no_regression_within_threshold(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines", threshold=5.0)
        baseline = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=80.0)
        d.save_baseline(baseline)
        current = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=76.0)
        report = d.check(current)
        assert report.regressed is False

    def test_improvement_not_regression(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        d.save_baseline(EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=70.0))
        current = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=90.0)
        report = d.check(current)
        assert report.regressed is False
        assert report.delta == 20.0

    def test_list_baselines(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        d.save_baseline(EvalRunResult(agent_id="x", rubric_name="r", aggregate_score=50.0))
        d.save_baseline(EvalRunResult(agent_id="y", rubric_name="r", aggregate_score=60.0))
        assert d.list_baselines() == ["x", "y"]

    def test_delete_baseline(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        d.save_baseline(EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=50.0))
        assert d.delete_baseline("a") is True
        assert d.delete_baseline("a") is False
        assert d.load_baseline("a") is None

    def test_threshold_property(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines", threshold=10.0)
        assert d.threshold == 10.0
        d.threshold = 3.0
        assert d.threshold == 3.0

    def test_report_to_dict(self, tmp_path: Path) -> None:
        d = RegressionDetector(baselines_dir=tmp_path / "baselines")
        current = EvalRunResult(agent_id="a", rubric_name="r", aggregate_score=75.0)
        report = d.check(current)
        dct = report.to_dict()
        assert "agent_id" in dct
        assert "regressed" in dct
