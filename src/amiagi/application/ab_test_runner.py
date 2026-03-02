"""ABTestRunner — compares two agent configurations on identical tasks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from amiagi.application.eval_runner import AgentCallable, EvalRunner, EvalScenario
from amiagi.domain.eval_rubric import EvalRubric


@dataclass
class ABComparisonResult:
    """Result of an A/B test between two agent configs."""

    agent_a_id: str
    agent_b_id: str
    rubric_name: str
    scenarios_count: int = 0
    a_wins: int = 0
    b_wins: int = 0
    ties: int = 0
    a_aggregate: float = 0.0
    b_aggregate: float = 0.0
    score_delta: float = 0.0  # a_aggregate - b_aggregate
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    per_scenario: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_a_id": self.agent_a_id,
            "agent_b_id": self.agent_b_id,
            "rubric_name": self.rubric_name,
            "scenarios_count": self.scenarios_count,
            "a_wins": self.a_wins,
            "b_wins": self.b_wins,
            "ties": self.ties,
            "a_aggregate": self.a_aggregate,
            "b_aggregate": self.b_aggregate,
            "score_delta": self.score_delta,
            "per_scenario": self.per_scenario,
        }


class ABTestRunner:
    """Compares two agent configurations on identical scenarios.

    Usage::

        runner = ABTestRunner(rubric=rubric)
        result = runner.compare("agent_v1", fn_a, "agent_v2", fn_b, scenarios)
    """

    def __init__(self, *, rubric: EvalRubric, pass_threshold: float = 50.0) -> None:
        self._rubric = rubric
        self._pass_threshold = pass_threshold
        self._history: list[ABComparisonResult] = []

    def compare(
        self,
        agent_a_id: str,
        agent_a_fn: AgentCallable,
        agent_b_id: str,
        agent_b_fn: AgentCallable,
        scenarios: list[EvalScenario],
    ) -> ABComparisonResult:
        """Run both agents on all scenarios, return comparison."""
        eval_a = EvalRunner(rubric=self._rubric, pass_threshold=self._pass_threshold)
        eval_b = EvalRunner(rubric=self._rubric, pass_threshold=self._pass_threshold)

        result_a = eval_a.run(agent_a_id, agent_a_fn, scenarios)
        result_b = eval_b.run(agent_b_id, agent_b_fn, scenarios)

        comp = ABComparisonResult(
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            rubric_name=self._rubric.name,
            scenarios_count=len(scenarios),
            a_aggregate=result_a.aggregate_score,
            b_aggregate=result_b.aggregate_score,
        )

        for ra, rb, sc in zip(result_a.results, result_b.results, scenarios):
            if ra.aggregate > rb.aggregate:
                comp.a_wins += 1
                winner = agent_a_id
            elif rb.aggregate > ra.aggregate:
                comp.b_wins += 1
                winner = agent_b_id
            else:
                comp.ties += 1
                winner = "tie"

            comp.per_scenario.append({
                "scenario_id": sc.scenario_id,
                "a_score": ra.aggregate,
                "b_score": rb.aggregate,
                "winner": winner,
            })

        comp.score_delta = round(comp.a_aggregate - comp.b_aggregate, 2)
        comp.finished_at = time.time()
        self._history.append(comp)
        return comp

    def history(self) -> list[ABComparisonResult]:
        return list(self._history)
