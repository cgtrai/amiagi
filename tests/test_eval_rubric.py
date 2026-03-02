"""Tests for EvalRubric domain model."""

from __future__ import annotations

from pathlib import Path

from amiagi.domain.eval_rubric import Criterion, EvalRubric


class TestCriterion:
    def test_defaults(self) -> None:
        c = Criterion(name="accuracy")
        assert c.weight == 1.0
        assert c.max_score == 5.0

    def test_roundtrip_dict(self) -> None:
        c = Criterion(name="style", description="Code style", weight=2.0, max_score=10.0)
        restored = Criterion.from_dict(c.to_dict())
        assert restored.name == "style"
        assert restored.weight == 2.0


class TestEvalRubric:
    def test_add_and_list_criteria(self) -> None:
        r = EvalRubric(name="test")
        r.add_criterion(Criterion(name="a"))
        r.add_criterion(Criterion(name="b"))
        assert r.criterion_names() == ["a", "b"]

    def test_remove_criterion(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="x"))
        r.add_criterion(Criterion(name="y"))
        r.remove_criterion("x")
        assert r.criterion_names() == ["y"]

    def test_get_criterion(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="q", weight=3.0))
        assert r.get_criterion("q") is not None
        assert r.get_criterion("q").weight == 3.0  # type: ignore[union-attr]
        assert r.get_criterion("nope") is None

    def test_score_perfect(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="c1", weight=1.0, max_score=5.0))
        result = r.score({"c1": 5.0})
        assert result.aggregate == 100.0

    def test_score_zero(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="c1", max_score=5.0))
        result = r.score({"c1": 0.0})
        assert result.aggregate == 0.0

    def test_score_weighted(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="a", weight=2.0, max_score=10.0))
        r.add_criterion(Criterion(name="b", weight=1.0, max_score=10.0))
        result = r.score({"a": 10.0, "b": 0.0})
        # a contributes 2/3 * 100 = 66.67
        assert abs(result.aggregate - 66.67) < 0.1

    def test_score_clamps_to_max(self) -> None:
        r = EvalRubric()
        r.add_criterion(Criterion(name="x", max_score=5.0))
        result = r.score({"x": 999.0})
        assert result.aggregate == 100.0

    def test_score_empty_rubric(self) -> None:
        r = EvalRubric()
        result = r.score({"anything": 5.0})
        assert result.aggregate == 0.0

    def test_roundtrip_dict(self) -> None:
        r = EvalRubric(name="test", description="desc")
        r.add_criterion(Criterion(name="c1", weight=2.0))
        restored = EvalRubric.from_dict(r.to_dict())
        assert restored.name == "test"
        assert len(restored.criteria) == 1
        assert restored.criteria[0].weight == 2.0

    def test_save_load_json(self, tmp_path: Path) -> None:
        r = EvalRubric(name="persist_test")
        r.add_criterion(Criterion(name="q1", weight=3.0))
        path = tmp_path / "rubric.json"
        r.save_json(path)
        loaded = EvalRubric.load_json(path)
        assert loaded.name == "persist_test"
        assert len(loaded.criteria) == 1
