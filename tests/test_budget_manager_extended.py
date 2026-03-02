"""Tests for Phase 8-11 gap-fill: budget (task/session), REST auth, SSE,
TeamComposer personas, DynamicScaler apply, CI GitHub, BenchmarkSuite YAML.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amiagi.application.budget_manager import (
    BudgetManager,
    BudgetRecord,
    SessionBudget,
    TaskBudget,
)


# ====================================================================
# 1. Per-task budgets
# ====================================================================


def test_set_and_get_task_budget() -> None:
    mgr = BudgetManager()
    mgr.set_task_budget("task-1", 2.0)
    tb = mgr.get_task_budget("task-1")
    assert tb is not None
    assert tb.limit_usd == 2.0
    assert tb.spent_usd == 0.0


def test_check_task_budget_within_limit() -> None:
    mgr = BudgetManager()
    mgr.set_task_budget("task-1", 1.0)
    assert mgr.check_task_budget("task-1", estimated_cost=0.5) is True


def test_check_task_budget_over_limit() -> None:
    mgr = BudgetManager()
    mgr.set_task_budget("task-1", 1.0)
    mgr.record_task_usage("task-1", cost_usd=0.8, tokens=100)
    assert mgr.check_task_budget("task-1", estimated_cost=0.5) is False


def test_check_task_budget_unlimited() -> None:
    mgr = BudgetManager()
    # No task budget set → always allowed
    assert mgr.check_task_budget("task-unknown", estimated_cost=999.0) is True


def test_record_task_usage_auto_creates() -> None:
    mgr = BudgetManager()
    mgr.record_task_usage("task-auto", cost_usd=0.1, tokens=50)
    tb = mgr.get_task_budget("task-auto")
    assert tb is not None
    assert tb.spent_usd == 0.1
    assert tb.tokens_used == 50
    assert tb.requests_count == 1


def test_task_summary() -> None:
    mgr = BudgetManager()
    mgr.set_task_budget("t1", 5.0)
    mgr.record_task_usage("t1", cost_usd=1.5, tokens=200)
    summary = mgr.task_summary()
    assert "t1" in summary
    assert summary["t1"]["spent_usd"] == 1.5
    assert summary["t1"]["limit_usd"] == 5.0
    assert summary["t1"]["utilization_pct"] == 30.0


def test_task_budget_utilization() -> None:
    tb = TaskBudget(task_id="x", limit_usd=10.0, spent_usd=8.0)
    assert tb.utilization_pct == 80.0
    assert tb.remaining_usd == 2.0


def test_task_budget_unlimited_remaining() -> None:
    tb = TaskBudget(task_id="x", limit_usd=0, spent_usd=5.0)
    assert tb.remaining_usd == float("inf")
    assert tb.utilization_pct == 0.0


# ====================================================================
# 2. Session budgets
# ====================================================================


def test_set_session_budget() -> None:
    mgr = BudgetManager()
    mgr.set_session_budget(100.0)
    assert mgr.session_budget.limit_usd == 100.0


def test_check_session_budget_within() -> None:
    mgr = BudgetManager()
    mgr.set_session_budget(10.0)
    assert mgr.check_session_budget(5.0) is True


def test_check_session_budget_over() -> None:
    mgr = BudgetManager()
    mgr.set_session_budget(1.0)
    mgr.record_session_usage(cost_usd=0.8, tokens=100)
    assert mgr.check_session_budget(0.5) is False


def test_check_session_budget_unlimited() -> None:
    mgr = BudgetManager()
    # Default limit=0.0 → unlimited
    assert mgr.check_session_budget(999.0) is True


def test_record_session_usage() -> None:
    mgr = BudgetManager()
    mgr.record_session_usage(cost_usd=0.5, tokens=300)
    mgr.record_session_usage(cost_usd=0.3, tokens=200)
    sb = mgr.session_budget
    assert sb.spent_usd == 0.8
    assert sb.tokens_used == 500
    assert sb.requests_count == 2


def test_session_summary() -> None:
    mgr = BudgetManager()
    mgr.set_session_budget(50.0)
    mgr.record_session_usage(cost_usd=10.0, tokens=1000)
    ss = mgr.session_summary()
    assert ss["limit_usd"] == 50.0
    assert ss["spent_usd"] == 10.0
    assert ss["utilization_pct"] == 20.0
    assert ss["tokens_used"] == 1000
    assert "started_at" in ss


def test_session_budget_utilization() -> None:
    sb = SessionBudget(limit_usd=20.0, spent_usd=16.0)
    assert sb.utilization_pct == 80.0
    assert sb.remaining_usd == 4.0


# ====================================================================
# 3. Combined record_usage_full
# ====================================================================


def test_record_usage_full() -> None:
    mgr = BudgetManager()
    mgr.set_budget("agent-1", 10.0)
    mgr.set_task_budget("task-1", 5.0)
    mgr.set_session_budget(50.0)

    mgr.record_usage_full("agent-1", task_id="task-1", cost_usd=1.0, tokens=500)

    assert mgr.get_budget("agent-1") is not None
    assert mgr.get_budget("agent-1").spent_usd == 1.0  # type: ignore[union-attr]
    assert mgr.get_task_budget("task-1") is not None
    assert mgr.get_task_budget("task-1").spent_usd == 1.0  # type: ignore[union-attr]
    assert mgr.session_budget.spent_usd == 1.0


def test_record_usage_full_without_task() -> None:
    mgr = BudgetManager()
    mgr.set_budget("agent-1", 10.0)
    mgr.set_session_budget(50.0)

    mgr.record_usage_full("agent-1", cost_usd=2.0, tokens=300)

    assert mgr.get_budget("agent-1").spent_usd == 2.0  # type: ignore[union-attr]
    assert mgr.session_budget.spent_usd == 2.0
