"""BudgetManager — per-agent and per-session cost governance."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


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
                "remaining_usd": rec.remaining_usd,
                "utilization_pct": rec.utilization_pct,
                "tokens_used": rec.tokens_used,
                "requests_count": rec.requests_count,
            }
        return result
