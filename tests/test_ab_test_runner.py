"""Tests for ABTestRunner."""

from __future__ import annotations

from amiagi.application.ab_test_runner import ABTestRunner
from amiagi.application.eval_runner import EvalScenario
from amiagi.domain.eval_rubric import Criterion, EvalRubric


def _make_rubric() -> EvalRubric:
    r = EvalRubric(name="ab_test")
    r.add_criterion(Criterion(name="quality", weight=1.0, max_score=5.0))
    return r


class TestABTestRunner:
    def test_a_always_wins(self) -> None:
        def good_agent(prompt: str) -> str:
            return "python fastapi sqlalchemy"

        def bad_agent(prompt: str) -> str:
            return "irrelevant response"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(
                scenario_id="s1",
                prompt="code review",
                expected_keywords=["python", "fastapi"],
            ),
        ]
        result = runner.compare("good", good_agent, "bad", bad_agent, scenarios)
        assert result.a_wins >= 1
        assert result.score_delta > 0

    def test_tie(self) -> None:
        def same(prompt: str) -> str:
            return "identical response"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["identical"]),
        ]
        result = runner.compare("a", same, "b", same, scenarios)
        assert result.ties >= 1
        assert result.score_delta == 0

    def test_b_wins(self) -> None:
        def weak(prompt: str) -> str:
            return "nothing relevant"

        def strong(prompt: str) -> str:
            return "python is great"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="q", expected_keywords=["python"]),
        ]
        result = runner.compare("weak", weak, "strong", strong, scenarios)
        assert result.b_wins >= 1
        assert result.score_delta < 0

    def test_history(self) -> None:
        def agent(prompt: str) -> str:
            return "ok"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [EvalScenario(scenario_id="s1", prompt="test")]
        runner.compare("a", agent, "b", agent, scenarios)
        assert len(runner.history()) == 1

    def test_per_scenario_detail(self) -> None:
        def agent(prompt: str) -> str:
            return "response"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="q1"),
            EvalScenario(scenario_id="s2", prompt="q2"),
        ]
        result = runner.compare("a", agent, "b", agent, scenarios)
        assert len(result.per_scenario) == 2
        assert result.scenarios_count == 2

    def test_to_dict(self) -> None:
        def agent(prompt: str) -> str:
            return "response"

        runner = ABTestRunner(rubric=_make_rubric())
        scenarios = [EvalScenario(scenario_id="s1", prompt="q")]
        result = runner.compare("a", agent, "b", agent, scenarios)
        d = result.to_dict()
        assert "agent_a_id" in d
        assert "score_delta" in d
