"""RegressionDetector — compares eval results against a baseline."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amiagi.application.eval_runner import EvalRunResult


@dataclass
class RegressionReport:
    """Report from comparing current eval vs baseline."""

    agent_id: str
    baseline_score: float
    current_score: float
    delta: float  # current - baseline
    regressed: bool  # True if delta < -threshold
    threshold: float = 5.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "baseline_score": self.baseline_score,
            "current_score": self.current_score,
            "delta": self.delta,
            "regressed": self.regressed,
            "threshold": self.threshold,
        }


class RegressionDetector:
    """Detects quality regressions by comparing against saved baselines.

    Usage::

        detector = RegressionDetector(baselines_dir=Path("./data/baselines"))
        detector.save_baseline(eval_result)
        report = detector.check(new_eval_result)
        if report.regressed:
            alert(...)
    """

    def __init__(
        self,
        *,
        baselines_dir: Path = Path("./data/baselines"),
        threshold: float = 5.0,
    ) -> None:
        self._dir = baselines_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._threshold = threshold
        self._lock = threading.Lock()

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = max(0.0, value)

    def save_baseline(self, result: EvalRunResult) -> Path:
        """Save an eval result as the baseline for its agent."""
        path = self._baseline_path(result.agent_id)
        path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def load_baseline(self, agent_id: str) -> EvalRunResult | None:
        """Load the saved baseline for *agent_id*."""
        path = self._baseline_path(agent_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return EvalRunResult(
                agent_id=data.get("agent_id", agent_id),
                rubric_name=data.get("rubric_name", ""),
                scenarios_count=data.get("scenarios_count", 0),
                passed=data.get("passed", 0),
                failed=data.get("failed", 0),
                aggregate_score=float(data.get("aggregate_score", 0.0)),
                metadata=data.get("metadata", {}),
            )
        except (json.JSONDecodeError, OSError):
            return None

    def check(self, current: EvalRunResult) -> RegressionReport:
        """Compare *current* eval result against saved baseline."""
        baseline = self.load_baseline(current.agent_id)
        baseline_score = baseline.aggregate_score if baseline else 0.0

        delta = round(current.aggregate_score - baseline_score, 2)
        regressed = delta < -self._threshold

        return RegressionReport(
            agent_id=current.agent_id,
            baseline_score=baseline_score,
            current_score=current.aggregate_score,
            delta=delta,
            regressed=regressed,
            threshold=self._threshold,
        )

    def list_baselines(self) -> list[str]:
        """Return agent IDs with saved baselines."""
        return sorted(
            p.stem for p in self._dir.glob("*.json")
        )

    def delete_baseline(self, agent_id: str) -> bool:
        path = self._baseline_path(agent_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ---- internals ----

    def _baseline_path(self, agent_id: str) -> Path:
        return self._dir / f"{agent_id}.json"
