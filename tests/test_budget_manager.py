"""Tests for BudgetManager."""

from __future__ import annotations

from amiagi.application.budget_manager import BudgetManager, BudgetRecord


class TestBudgetRecord:
    def test_remaining_unlimited(self) -> None:
        r = BudgetRecord(agent_id="a", limit_usd=0, spent_usd=10)
        assert r.remaining_usd == float("inf")

    def test_remaining_with_limit(self) -> None:
        r = BudgetRecord(agent_id="a", limit_usd=5.0, spent_usd=3.0)
        assert r.remaining_usd == 2.0

    def test_utilization_pct(self) -> None:
        r = BudgetRecord(agent_id="a", limit_usd=10.0, spent_usd=8.0)
        assert r.utilization_pct == 80.0

    def test_utilization_unlimited(self) -> None:
        r = BudgetRecord(agent_id="a", limit_usd=0)
        assert r.utilization_pct == 0.0


class TestBudgetManager:
    def test_no_budget_always_allowed(self) -> None:
        mgr = BudgetManager()
        assert mgr.check_budget("unknown", 100.0) is True

    def test_within_budget_allowed(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=5.0, tokens=100)
        assert mgr.check_budget("a", 4.0) is True

    def test_over_budget_blocked(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("a", 1.0)
        mgr.record_usage("a", cost_usd=0.9, tokens=100)
        assert mgr.check_budget("a", 0.2) is False

    def test_warning_callback_at_80pct(self) -> None:
        warned: list[str] = []
        mgr = BudgetManager(on_warning=lambda r: warned.append(r.agent_id))
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=8.1, tokens=100)
        assert "a" in warned

    def test_exhausted_callback_at_100pct(self) -> None:
        exhausted: list[str] = []
        mgr = BudgetManager(on_exhausted=lambda r: exhausted.append(r.agent_id))
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=10.1, tokens=100)
        assert "a" in exhausted

    def test_warning_fired_once(self) -> None:
        count = []
        mgr = BudgetManager(on_warning=lambda r: count.append(1))
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=8.5, tokens=100)
        mgr.record_usage("a", cost_usd=0.5, tokens=50)
        assert len(count) == 1

    def test_reset_agent(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=5.0, tokens=100)
        mgr.reset_agent("a")
        rec = mgr.get_budget("a")
        assert rec is not None
        assert rec.spent_usd == 0.0
        assert rec.tokens_used == 0

    def test_reset_all(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("a", 10.0)
        mgr.set_budget("b", 20.0)
        mgr.record_usage("a", cost_usd=5.0)
        mgr.record_usage("b", cost_usd=15.0)
        mgr.reset_all()
        assert mgr.get_budget("a").spent_usd == 0.0  # type: ignore[union-attr]
        assert mgr.get_budget("b").spent_usd == 0.0  # type: ignore[union-attr]

    def test_list_budgets(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("x", 5.0)
        mgr.set_budget("y", 10.0)
        assert len(mgr.list_budgets()) == 2

    def test_summary(self) -> None:
        mgr = BudgetManager()
        mgr.set_budget("a", 10.0)
        mgr.record_usage("a", cost_usd=2.0, tokens=500)
        s = mgr.summary()
        assert "a" in s
        assert s["a"]["spent_usd"] == 2.0
        assert s["a"]["tokens_used"] == 500

    def test_record_without_budget_creates_record(self) -> None:
        mgr = BudgetManager()
        mgr.record_usage("new_agent", cost_usd=1.0, tokens=100)
        rec = mgr.get_budget("new_agent")
        assert rec is not None
        assert rec.spent_usd == 1.0

    def test_requests_count_increments(self) -> None:
        mgr = BudgetManager()
        mgr.record_usage("a", cost_usd=0.1)
        mgr.record_usage("a", cost_usd=0.2)
        mgr.record_usage("a", cost_usd=0.3)
        rec = mgr.get_budget("a")
        assert rec is not None
        assert rec.requests_count == 3
