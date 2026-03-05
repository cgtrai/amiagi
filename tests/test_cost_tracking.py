"""Tests for cost tracking (BudgetManager, estimate_cost, API) — P12 Cost Tracking."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from amiagi.application.budget_manager import (
    BudgetManager,
    BudgetRecord,
    MODEL_PRICING,
    estimate_cost,
)
from amiagi.interfaces.web.routes.api_routes import get_budget_tasks


def _make_app(bm: BudgetManager | None = None) -> Starlette:
    app = Starlette(routes=[
        Route("/api/budget/tasks", get_budget_tasks, methods=["GET"]),
    ])
    if bm is not None:
        app.state.budget_manager = bm
    return app


# ── estimate_cost ────────────────────────────────────────────────


class TestEstimateCost:
    def test_known_model(self) -> None:
        cost = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=1000)
        assert cost > 0

    def test_unknown_model_returns_zero(self) -> None:
        assert estimate_cost("nonexistent-model", input_tokens=1000) == 0.0

    def test_local_model_free(self) -> None:
        assert estimate_cost("llama3.1:8b", input_tokens=5000, output_tokens=5000) == 0.0

    def test_zero_tokens(self) -> None:
        assert estimate_cost("gpt-4o", input_tokens=0, output_tokens=0) == 0.0


class TestModelPricing:
    def test_has_ollama_models(self) -> None:
        assert "llama3.1:8b" in MODEL_PRICING

    def test_has_openai_models(self) -> None:
        assert "gpt-4o" in MODEL_PRICING

    def test_has_anthropic_models(self) -> None:
        assert "claude-sonnet-4-20250514" in MODEL_PRICING

    def test_each_entry_has_input_output(self) -> None:
        for model, pricing in MODEL_PRICING.items():
            assert "input" in pricing, f"{model} missing 'input'"
            assert "output" in pricing, f"{model} missing 'output'"


# ── BudgetManager per-task tracking ─────────────────────────────


class TestTaskBudget:
    def test_record_and_summary(self) -> None:
        bm = BudgetManager()
        bm.record_task_usage("task-1", cost_usd=0.05, tokens=500)
        bm.record_task_usage("task-1", cost_usd=0.03, tokens=300)
        summary = bm.task_summary()
        assert "task-1" in summary
        assert summary["task-1"]["spent_usd"] == pytest.approx(0.08)
        assert summary["task-1"]["tokens_used"] == 800

    def test_set_task_budget_limits(self) -> None:
        bm = BudgetManager()
        bm.set_task_budget("task-2", 1.0)
        assert bm.check_task_budget("task-2", estimated_cost=0.5) is True
        assert bm.check_task_budget("task-2", estimated_cost=1.5) is False

    def test_no_budget_always_allowed(self) -> None:
        bm = BudgetManager()
        assert bm.check_task_budget("unknown-task", estimated_cost=999) is True

    def test_record_usage_full(self) -> None:
        bm = BudgetManager()
        bm.record_usage_full("agent-1", task_id="task-3", cost_usd=0.1, tokens=100)
        assert bm.task_summary()["task-3"]["spent_usd"] == pytest.approx(0.1)
        assert bm.session_summary()["spent_usd"] == pytest.approx(0.1)


# ── GET /api/budget/tasks endpoint ──────────────────────────────


class TestBudgetTasksEndpoint:
    def test_returns_empty_when_no_manager(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/budget/tasks")
        assert r.status_code == 200
        assert r.json() == {"tasks": {}}

    def test_returns_task_data(self) -> None:
        bm = BudgetManager()
        bm.record_task_usage("build-api", cost_usd=0.25, tokens=2000)
        client = TestClient(_make_app(bm))
        r = client.get("/api/budget/tasks")
        assert r.status_code == 200
        data = r.json()
        assert "build-api" in data["tasks"]
        assert data["tasks"]["build-api"]["spent_usd"] == pytest.approx(0.25)
