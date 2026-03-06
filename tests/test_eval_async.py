"""Tests for EvalRunner.run_async + ABTestRunner.compare_async — Sprint P3 item 4.8."""

from __future__ import annotations

import asyncio
import pytest

from amiagi.application.eval_runner import EvalRunner, EvalScenario
from amiagi.application.ab_test_runner import ABTestRunner
from amiagi.domain.eval_rubric import EvalRubric, Criterion


def _make_rubric() -> EvalRubric:
    return EvalRubric(
        name="test",
        criteria=[Criterion(name="accuracy", max_score=100)],
    )


def _make_scenarios() -> list[EvalScenario]:
    return [
        EvalScenario(scenario_id="s1", prompt="Hello", expected_keywords=["hello"]),
    ]


def _agent(prompt: str) -> str:
    return "hello world"


@pytest.mark.asyncio
async def test_eval_runner_run_async() -> None:
    runner = EvalRunner(rubric=_make_rubric())
    result = await runner.run_async("test-agent", _agent, _make_scenarios())
    assert result.agent_id == "test-agent"
    assert result.finished_at is not None
    assert result.scenarios_count == 1


@pytest.mark.asyncio
async def test_ab_runner_compare_async() -> None:
    runner = ABTestRunner(rubric=_make_rubric())
    result = await runner.compare_async(
        "agent_a", _agent, "agent_b", _agent, _make_scenarios(),
    )
    assert result.agent_a_id == "agent_a"
    assert result.agent_b_id == "agent_b"
    assert result.finished_at is not None
    assert result.scenarios_count == 1
