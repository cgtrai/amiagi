"""Tests for EvalRunner."""

from __future__ import annotations

from amiagi.application.eval_runner import (
    EvalRunner,
    EvalScenario,
    llm_judge_scorer,
    make_llm_judge_scorer,
)
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
        def broken(prompt: str) -> str:
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


class TestLLMJudgeScorer:
    def test_fallback_when_no_judge_fn(self) -> None:
        scenario = EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["hello"])
        rubric = _make_rubric()
        scores = llm_judge_scorer("hello world", scenario, rubric, judge_fn=None)
        assert "correctness" in scores
        assert scores["correctness"] > 0

    def test_judge_fn_returns_valid_json(self) -> None:
        def mock_judge(prompt: str) -> str:
            return '{"correctness": 4.0, "completeness": 3.5}'

        scenario = EvalScenario(scenario_id="s1", prompt="test")
        rubric = _make_rubric()
        scores = llm_judge_scorer("some response", scenario, rubric, judge_fn=mock_judge)
        assert scores["correctness"] == 4.0
        assert scores["completeness"] == 3.5

    def test_judge_fn_raises_falls_back(self) -> None:
        def failing_judge(prompt: str) -> str:
            raise RuntimeError("LLM unavailable")

        scenario = EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["response"])
        rubric = _make_rubric()
        scores = llm_judge_scorer("response here", scenario, rubric, judge_fn=failing_judge)
        # Should fall back to keyword scorer
        assert "correctness" in scores

    def test_judge_fn_returns_garbage_falls_back(self) -> None:
        def garbage_judge(prompt: str) -> str:
            return "I don't know how to respond"

        scenario = EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["keyword"])
        rubric = _make_rubric()
        scores = llm_judge_scorer("keyword present", scenario, rubric, judge_fn=garbage_judge)
        assert "correctness" in scores

    def test_make_llm_judge_scorer(self) -> None:
        def mock_judge(prompt: str) -> str:
            return '{"correctness": 5.0, "completeness": 5.0}'

        scorer = make_llm_judge_scorer(mock_judge)
        scenario = EvalScenario(scenario_id="s1", prompt="test")
        rubric = _make_rubric()
        scores = scorer("response", scenario, rubric)
        assert scores["correctness"] == 5.0

    def test_scores_clamped_to_max(self) -> None:
        def inflated_judge(prompt: str) -> str:
            return '{"correctness": 999.0, "completeness": 999.0}'

        scenario = EvalScenario(scenario_id="s1", prompt="test")
        rubric = _make_rubric()
        scores = llm_judge_scorer("response", scenario, rubric, judge_fn=inflated_judge)
        assert scores["correctness"] <= 5.0
        assert scores["completeness"] <= 5.0
