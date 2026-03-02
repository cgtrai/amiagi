"""AgentTestRunner — validates a freshly-created agent against test scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amiagi.domain.blueprint import TestScenario
from amiagi.infrastructure.agent_runtime import AgentRuntime


@dataclass(frozen=True)
class ScenarioResult:
    """Result of a single test scenario."""

    scenario_name: str
    passed: bool
    response: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class ValidationReport:
    """Aggregated test results for an agent."""

    agent_id: str
    agent_name: str
    total: int
    passed: int
    failed: int
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.passed / max(self.total, 1)

    def summary(self) -> str:
        return (
            f"Agent {self.agent_name!r}: {self.passed}/{self.total} scenarios passed "
            f"({self.success_rate:.0%})"
        )


class AgentTestRunner:
    """Runs test scenarios against an :class:`AgentRuntime` and reports results."""

    def validate(
        self,
        runtime: AgentRuntime,
        scenarios: list[TestScenario],
    ) -> ValidationReport:
        """Execute all *scenarios* and return a :class:`ValidationReport`."""
        results: list[ScenarioResult] = []
        for scenario in scenarios:
            result = self._run_scenario(runtime, scenario)
            results.append(result)

        passed = sum(1 for r in results if r.passed)
        return ValidationReport(
            agent_id=runtime.agent_id,
            agent_name=runtime.descriptor.name,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            results=results,
        )

    def _run_scenario(
        self,
        runtime: AgentRuntime,
        scenario: TestScenario,
    ) -> ScenarioResult:
        """Run a single scenario and evaluate the response."""
        try:
            response = runtime.ask(scenario.prompt, actor="TestRunner")
        except Exception as exc:
            return ScenarioResult(
                scenario_name=scenario.name,
                passed=False,
                response="",
                notes=f"Error: {exc}",
            )

        response_lower = response.lower()
        matched: list[str] = []
        missing: list[str] = []
        for kw in scenario.expected_keywords:
            if kw.lower() in response_lower:
                matched.append(kw)
            else:
                missing.append(kw)

        # Pass if at least 50% of expected keywords are present
        keyword_threshold = len(scenario.expected_keywords) / 2 if scenario.expected_keywords else 0
        passed = len(matched) >= keyword_threshold if scenario.expected_keywords else True

        return ScenarioResult(
            scenario_name=scenario.name,
            passed=passed,
            response=response[:2000],
            matched_keywords=matched,
            missing_keywords=missing,
        )
