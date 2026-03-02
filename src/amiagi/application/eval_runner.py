"""EvalRunner — runs evaluation scenarios against agents and scores them."""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from amiagi.domain.eval_rubric import EvalResult, EvalRubric


@dataclass
class EvalScenario:
    """A single test scenario for evaluation."""

    scenario_id: str
    prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRunResult:
    """Result of running an evaluation suite."""

    agent_id: str
    rubric_name: str
    results: list[EvalResult] = field(default_factory=list)
    scenarios_count: int = 0
    passed: int = 0
    failed: int = 0
    aggregate_score: float = 0.0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "rubric_name": self.rubric_name,
            "scenarios_count": self.scenarios_count,
            "passed": self.passed,
            "failed": self.failed,
            "aggregate_score": self.aggregate_score,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [r.to_dict() for r in self.results],
            "metadata": self.metadata,
        }


class AgentCallable(Protocol):
    """Protocol for agent invocation."""

    def __call__(self, prompt: str) -> str: ...


# Default scorer: keyword match heuristic
def keyword_scorer(
    response: str,
    scenario: EvalScenario,
    rubric: EvalRubric,
) -> dict[str, float]:
    """Score based on keyword presence in response."""
    response_lower = response.lower()
    if not scenario.expected_keywords:
        # All criteria get max score if no keywords specified
        return {c.name: c.max_score for c in rubric.criteria}

    hit_ratio = sum(
        1 for kw in scenario.expected_keywords if kw.lower() in response_lower
    ) / max(len(scenario.expected_keywords), 1)

    return {c.name: round(c.max_score * hit_ratio, 2) for c in rubric.criteria}


class EvalRunner:
    """Evaluates agents against scenarios using a rubric.

    Supports pluggable scorer functions (default: keyword matching)
    and optional LLM-as-judge scoring.

    Usage::

        runner = EvalRunner(rubric=rubric)
        result = runner.run("polluks", agent_fn, scenarios)
    """

    def __init__(
        self,
        *,
        rubric: EvalRubric,
        scorer: Callable[
            [str, EvalScenario, EvalRubric], dict[str, float]
        ] | None = None,
        pass_threshold: float = 50.0,
    ) -> None:
        self._rubric = rubric
        self._scorer = scorer or keyword_scorer
        self._pass_threshold = pass_threshold
        self._lock = threading.Lock()
        self._history: list[EvalRunResult] = []

    @property
    def rubric(self) -> EvalRubric:
        return self._rubric

    def run(
        self,
        agent_id: str,
        agent_fn: AgentCallable,
        scenarios: list[EvalScenario],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> EvalRunResult:
        """Execute all *scenarios* against the agent and score results."""
        run_result = EvalRunResult(
            agent_id=agent_id,
            rubric_name=self._rubric.name,
            scenarios_count=len(scenarios),
            metadata=metadata or {},
        )

        for scenario in scenarios:
            try:
                response = agent_fn(scenario.prompt)
            except Exception as exc:
                response = f"ERROR: {exc}"

            raw_scores = self._scorer(response, scenario, self._rubric)
            eval_result = self._rubric.score(
                raw_scores,
                notes={"response_preview": response[:200]},
                metadata={"scenario_id": scenario.scenario_id},
            )
            run_result.results.append(eval_result)

            if eval_result.aggregate >= self._pass_threshold:
                run_result.passed += 1
            else:
                run_result.failed += 1

        # Aggregate
        if run_result.results:
            run_result.aggregate_score = round(
                sum(r.aggregate for r in run_result.results) / len(run_result.results),
                2,
            )

        run_result.finished_at = time.time()

        with self._lock:
            self._history.append(run_result)

        return run_result

    def history(self, agent_id: str | None = None) -> list[EvalRunResult]:
        """Return evaluation history, optionally filtered by agent."""
        with self._lock:
            results = list(self._history)
        if agent_id is not None:
            results = [r for r in results if r.agent_id == agent_id]
        return results

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
