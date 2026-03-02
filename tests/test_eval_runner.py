"""Tests for EvalRunner."""

from __future__ import annotations

from amiagi.application.eval_runner import EvalRunner, EvalScenario
from amiagi.domain.eval_rubric import Criterion, EvalRubric


def _make_rubric() -> EvalRubric:
    r = EvalRubric(name="test")
    r.add_criterion(Criterion(name="correctness", weight=2.0, max_score=5.0))
    r.add_criterion(Criterion(name="completeness", weight=1.0, max_score=5.0))
    return r


def _echo_agent(prompt: str) -> str:
    return f"Response to: {prompt}"


def _keyword_agent(prompt: str) -> str:
    return "python fastapi sqlalchemy review"


class TestEvalRunner:
    def test_run_basic(self) -> None:
        runner = EvalRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["Response"]),
        ]
        result = runner.run("agent1", _echo_agent, scenarios)
        assert result.agent_id == "agent1"
        assert result.scenarios_count == 1
        assert result.finished_at is not None

    def test_keyword_scoring(self) -> None:
        runner = EvalRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(
                scenario_id="s1",
                prompt="review code",
                expected_keywords=["python", "fastapi", "sqlalchemy"],
            ),
        ]
        result = runner.run("agent1", _keyword_agent, scenarios)
        assert result.aggregate_score > 0
        assert result.passed >= 1

    def test_no_keywords_gives_max(self) -> None:
        runner = EvalRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="hello"),
        ]
        result = runner.run("agent1", _echo_agent, scenarios)
        assert result.aggregate_score == 100.0

    def test_failing_agent(self) -> None:
        def broken(_: str) -> str:
            raise RuntimeError("broken")

        runner = EvalRunner(rubric=_make_rubric())
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["success"]),
        ]
        result = runner.run("bad", broken, scenarios)
        assert result.scenarios_count == 1
        assert result.failed >= 0  # shouldn't crash

    def test_pass_threshold(self) -> None:
        runner = EvalRunner(rubric=_make_rubric(), pass_threshold=90.0)
        scenarios = [
            EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["missing"]),
        ]
        result = runner.run("agent", _echo_agent, scenarios)
        assert result.failed == 1

    def test_history(self) -> None:
        runner = EvalRunner(rubric=_make_rubric())
        scenarios = [EvalScenario(scenario_id="s1", prompt="hi")]
        runner.run("a", _echo_agent, scenarios)
        runner.run("b", _echo_agent, scenarios)
        assert len(runner.history()) == 2
        assert len(runner.history("a")) == 1

    def test_custom_scorer(self) -> None:
        def always_perfect(response, scenario, rubric):
            return {c.name: c.max_score for c in rubric.criteria}

        runner = EvalRunner(rubric=_make_rubric(), scorer=always_perfect)
        scenarios = [EvalScenario(scenario_id="s1", prompt="test")]
        result = runner.run("agent", _echo_agent, scenarios)
        assert result.aggregate_score == 100.0

    def test_rubric_property(self) -> None:
        rubric = _make_rubric()
        runner = EvalRunner(rubric=rubric)
        assert runner.rubric.name == "test"
