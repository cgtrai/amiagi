"""BudgetManager — per-agent, per-task and per-session cost governance."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


def _safe_float(v: float) -> float | None:
    """Return *v* unless it is infinite or NaN (not JSON-serialisable)."""
    if math.isinf(v) or math.isnan(v):
        return None
    return v


# ── Model pricing (USD per 1 K tokens) ──────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Ollama / local — effectively free
    "llama3.1:8b": {"input": 0.0, "output": 0.0},
    "llama3.2:3b": {"input": 0.0, "output": 0.0},
    "deepseek-r1:8b": {"input": 0.0, "output": 0.0},
    "mistral:7b": {"input": 0.0, "output": 0.0},
    "qwen2.5:7b": {"input": 0.0, "output": 0.0},
    # OpenAI
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku-20241022": {"input": 0.0008, "output": 0.004},
}


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """Estimate cost in USD for a single LLM call.

    Falls back to 0.0 if the model is not in :data:`MODEL_PRICING`.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    return (input_tokens / 1000) * pricing["input"] + (output_tokens / 1000) * pricing["output"]


@dataclass
class BudgetRecord:
    """Tracks spending for one agent."""

    agent_id: str
    limit_usd: float = 0.0  # 0 = unlimited
    spent_usd: float = 0.0
    tokens_used: int = 0
    requests_count: int = 0
    last_updated: float = field(default_factory=time.time)

    @property
    def remaining_usd(self) -> float:
        if self.limit_usd <= 0:
            return float("inf")
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def utilization_pct(self) -> float:
        if self.limit_usd <= 0:
            return 0.0
        return min(100.0, (self.spent_usd / self.limit_usd) * 100)


@dataclass
class TaskBudget:
    """Tracks spending for a single task."""

    task_id: str
    limit_usd: float = 0.0
    spent_usd: float = 0.0
    tokens_used: int = 0
    requests_count: int = 0

    @property
    def remaining_usd(self) -> float:
        if self.limit_usd <= 0:
            return float("inf")
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def utilization_pct(self) -> float:
        if self.limit_usd <= 0:
            return 0.0
        return min(100.0, (self.spent_usd / self.limit_usd) * 100)


@dataclass
class SessionBudget:
    """Tracks aggregate spending for the entire session."""

    limit_usd: float = 0.0
    spent_usd: float = 0.0
    tokens_used: int = 0
    requests_count: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def remaining_usd(self) -> float:
        if self.limit_usd <= 0:
            return float("inf")
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def utilization_pct(self) -> float:
        if self.limit_usd <= 0:
            return 0.0
        return min(100.0, (self.spent_usd / self.limit_usd) * 100)


class BudgetManager:
    """Tracks and enforces per-agent cost budgets.

    Fires callbacks at 80 % and 100 % thresholds so that an
    :class:`AlertManager` can react.

    Usage::

        mgr = BudgetManager()
        mgr.set_budget("polluks", 5.0)  # $5 daily limit
        if mgr.check_budget("polluks", estimated_cost=0.02):
            # ... proceed with LLM call
            mgr.record_usage("polluks", cost_usd=0.018, tokens=1200)
    """

    THRESHOLD_WARNING = 0.80
    THRESHOLD_BLOCKED = 1.00

    def __init__(
        self,
        *,
        on_warning: Callable[[BudgetRecord], None] | None = None,
        on_exhausted: Callable[[BudgetRecord], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._budgets: dict[str, BudgetRecord] = {}
        self._task_budgets: dict[str, TaskBudget] = {}
        self._session_budget: SessionBudget = SessionBudget()
        self._on_warning = on_warning
        self._on_exhausted = on_exhausted
        self._warned: set[str] = set()  # agents already warned at 80 %
        self._exhausted: set[str] = set()  # agents already notified at 100 %

    # ---- budget configuration ----

    def set_budget(self, agent_id: str, limit_usd: float) -> None:
        """Set or update the spending limit for *agent_id*."""
        with self._lock:
            rec = self._budgets.get(agent_id)
            if rec is None:
                rec = BudgetRecord(agent_id=agent_id, limit_usd=limit_usd)
                self._budgets[agent_id] = rec
            else:
                rec.limit_usd = limit_usd
            # reset alert flags if limit raised
            if agent_id in self._warned and rec.utilization_pct < self.THRESHOLD_WARNING * 100:
                self._warned.discard(agent_id)
            if agent_id in self._exhausted and rec.utilization_pct < self.THRESHOLD_BLOCKED * 100:
                self._exhausted.discard(agent_id)

    def get_budget(self, agent_id: str) -> BudgetRecord | None:
        return self._budgets.get(agent_id)

    def list_budgets(self) -> list[BudgetRecord]:
        return list(self._budgets.values())

    # ---- runtime checks ----

    def check_budget(self, agent_id: str, estimated_cost: float = 0.0) -> bool:
        """Return ``True`` if agent may proceed (within budget).

        If no budget is set for *agent_id*, always returns ``True``.
        """
        rec = self._budgets.get(agent_id)
        if rec is None or rec.limit_usd <= 0:
            return True
        return (rec.spent_usd + estimated_cost) <= rec.limit_usd

    def record_usage(
        self,
        agent_id: str,
        *,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> None:
        """Record actual usage and trigger alerts at thresholds."""
        with self._lock:
            rec = self._budgets.get(agent_id)
            if rec is None:
                rec = BudgetRecord(agent_id=agent_id)
                self._budgets[agent_id] = rec
            rec.spent_usd += cost_usd
            rec.tokens_used += tokens
            rec.requests_count += 1
            rec.last_updated = time.time()

        # Fire threshold callbacks outside the lock
        if rec.limit_usd > 0:
            pct = rec.utilization_pct
            if pct >= self.THRESHOLD_BLOCKED * 100 and agent_id not in self._exhausted:
                self._exhausted.add(agent_id)
                if self._on_exhausted:
                    self._on_exhausted(rec)
            elif pct >= self.THRESHOLD_WARNING * 100 and agent_id not in self._warned:
                self._warned.add(agent_id)
                if self._on_warning:
                    self._on_warning(rec)

    # ---- reset ----

    def reset_agent(self, agent_id: str) -> None:
        """Reset spending counters for *agent_id* (e.g. daily reset)."""
        with self._lock:
            rec = self._budgets.get(agent_id)
            if rec is not None:
                rec.spent_usd = 0.0
                rec.tokens_used = 0
                rec.requests_count = 0
                rec.last_updated = time.time()
            self._warned.discard(agent_id)
            self._exhausted.discard(agent_id)

    def reset_all(self) -> None:
        """Reset all agents' spending counters."""
        with self._lock:
            for rec in self._budgets.values():
                rec.spent_usd = 0.0
                rec.tokens_used = 0
                rec.requests_count = 0
                rec.last_updated = time.time()
            self._warned.clear()
            self._exhausted.clear()

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return per-agent budget summary."""
        result: dict[str, dict[str, Any]] = {}
        for aid, rec in self._budgets.items():
            result[aid] = {
                "limit_usd": rec.limit_usd,
                "spent_usd": rec.spent_usd,
                "remaining_usd": _safe_float(rec.remaining_usd),
                "utilization_pct": rec.utilization_pct,
                "tokens_used": rec.tokens_used,
                "requests_count": rec.requests_count,
            }
        return result

    # ================================================================
    # Per-task budgets
    # ================================================================

    def set_task_budget(self, task_id: str, limit_usd: float) -> None:
        """Set or update the spending limit for *task_id*."""
        with self._lock:
            tb = self._task_budgets.get(task_id)
            if tb is None:
                tb = TaskBudget(task_id=task_id, limit_usd=limit_usd)
                self._task_budgets[task_id] = tb
            else:
                tb.limit_usd = limit_usd

    def get_task_budget(self, task_id: str) -> TaskBudget | None:
        return self._task_budgets.get(task_id)

    def check_task_budget(self, task_id: str, estimated_cost: float = 0.0) -> bool:
        """Return ``True`` if the task may proceed (within budget)."""
        tb = self._task_budgets.get(task_id)
        if tb is None or tb.limit_usd <= 0:
            return True
        return (tb.spent_usd + estimated_cost) <= tb.limit_usd

    def record_task_usage(
        self,
        task_id: str,
        *,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> None:
        """Record actual usage for a task."""
        with self._lock:
            tb = self._task_budgets.get(task_id)
            if tb is None:
                tb = TaskBudget(task_id=task_id)
                self._task_budgets[task_id] = tb
            tb.spent_usd += cost_usd
            tb.tokens_used += tokens
            tb.requests_count += 1

    def task_summary(self) -> dict[str, dict[str, Any]]:
        """Return per-task budget summary."""
        result: dict[str, dict[str, Any]] = {}
        for tid, tb in self._task_budgets.items():
            result[tid] = {
                "limit_usd": tb.limit_usd,
                "spent_usd": tb.spent_usd,
                "remaining_usd": _safe_float(tb.remaining_usd),
                "utilization_pct": tb.utilization_pct,
                "tokens_used": tb.tokens_used,
                "requests_count": tb.requests_count,
            }
        return result

    # ================================================================
    # Session-level budget
    # ================================================================

    def set_session_budget(self, limit_usd: float) -> None:
        """Set the session-wide spending limit."""
        with self._lock:
            self._session_budget.limit_usd = limit_usd

    @property
    def session_budget(self) -> SessionBudget:
        return self._session_budget

    def check_session_budget(self, estimated_cost: float = 0.0) -> bool:
        """Return ``True`` if the session may proceed."""
        sb = self._session_budget
        if sb.limit_usd <= 0:
            return True
        return (sb.spent_usd + estimated_cost) <= sb.limit_usd

    def record_session_usage(
        self,
        *,
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> None:
        """Record session-level usage (call alongside agent-level recording)."""
        with self._lock:
            self._session_budget.spent_usd += cost_usd
            self._session_budget.tokens_used += tokens
            self._session_budget.requests_count += 1

    def session_summary(self) -> dict[str, Any]:
        """Return session budget summary."""
        sb = self._session_budget
        return {
            "limit_usd": sb.limit_usd,
            "spent_usd": sb.spent_usd,
            "remaining_usd": _safe_float(sb.remaining_usd),
            "utilization_pct": sb.utilization_pct,
            "tokens_used": sb.tokens_used,
            "requests_count": sb.requests_count,
            "started_at": sb.started_at,
        }

    # ================================================================
    # Combined record helper
    # ================================================================

    def record_usage_full(
        self,
        agent_id: str,
        *,
        task_id: str = "",
        cost_usd: float = 0.0,
        tokens: int = 0,
    ) -> None:
        """Record usage across agent, task and session in one call."""
        self.record_usage(agent_id, cost_usd=cost_usd, tokens=tokens)
        if task_id:
            self.record_task_usage(task_id, cost_usd=cost_usd, tokens=tokens)
        self.record_session_usage(cost_usd=cost_usd, tokens=tokens)
