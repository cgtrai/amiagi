"""Tests for Budget / Cost Center extended routes."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.budget_routes import budget_routes

_LOG_ACTION = "amiagi.interfaces.web.audit.log_helpers.log_action"


# ── Helpers ──────────────────────────────────────────────────

def _make_app(**state_attrs) -> TestClient:
    app = Starlette(routes=list(budget_routes))
    for k, v in state_attrs.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def _mock_budget_manager(
    *,
    agents: dict | None = None,
    session: dict | None = None,
) -> MagicMock:
    bm = MagicMock()
    agents = agents or {
        "kastor": {
            "spent_usd": 1.50,
            "limit_usd": 10.0,
            "tokens": 5000,
            "requests": 12,
            "utilization_pct": 15.0,
        },
        "polluks": {
            "spent_usd": 0.75,
            "limit_usd": 5.0,
            "tokens": 2000,
            "requests": 6,
            "utilization_pct": 15.0,
        },
    }
    session = session or {
        "spent_usd": 2.25,
        "limit_usd": 50.0,
        "tokens": 7000,
        "requests": 18,
    }
    bm.summary.return_value = agents
    bm.session_summary.return_value = session
    bm.reset_all.return_value = None
    bm.reset_agent.return_value = None
    bm.set_session_budget = MagicMock()
    return bm


# ── GET /api/budget/history ──────────────────────────────────

class TestBudgetHistory:
    def test_returns_agents_and_session(self) -> None:
        bm = _mock_budget_manager()
        client = _make_app(budget_manager=bm)
        r = client.get("/api/budget/history")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["agents"]) == 2
        assert data["session"]["spent_usd"] == 2.25
        agent_ids = [a["agent_id"] for a in data["agents"]]
        assert "kastor" in agent_ids
        assert "polluks" in agent_ids

    def test_no_budget_manager_returns_503(self) -> None:
        client = _make_app()
        r = client.get("/api/budget/history")
        assert r.status_code == 503

    def test_agents_contain_expected_fields(self) -> None:
        bm = _mock_budget_manager()
        client = _make_app(budget_manager=bm)
        data = client.get("/api/budget/history").json()
        agent = data["agents"][0]
        for field in ("agent_id", "spent_usd", "limit_usd", "tokens", "requests"):
            assert field in agent, f"Missing field: {field}"


# ── PUT /api/budget/quotas ───────────────────────────────────

class TestBudgetQuotas:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_save_session_limit(self, _mock_log) -> None:
        bm = _mock_budget_manager()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "budget_defaults.yaml"
            with patch(
                "amiagi.interfaces.web.routes.budget_routes._BUDGET_DEFAULTS_PATH",
                config_path,
            ):
                client = _make_app(budget_manager=bm)
                r = client.put(
                    "/api/budget/quotas",
                    json={"session_limit_usd": 100.0, "warning_pct": 80},
                )
                assert r.status_code == 200
                data = r.json()
                assert data["ok"] is True
                assert data["config"]["session"]["limit_usd"] == 100.0
                assert data["config"]["thresholds"]["warning_pct"] == 80

    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_save_blocked_pct(self, _mock_log) -> None:
        bm = _mock_budget_manager()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "budget_defaults.yaml"
            with patch(
                "amiagi.interfaces.web.routes.budget_routes._BUDGET_DEFAULTS_PATH",
                config_path,
            ):
                client = _make_app(budget_manager=bm)
                r = client.put(
                    "/api/budget/quotas",
                    json={"blocked_pct": 95},
                )
                assert r.status_code == 200
                data = r.json()
                assert data["config"]["thresholds"]["blocked_pct"] == 95

    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_save_per_agent_limit(self, _mock_log) -> None:
        bm = _mock_budget_manager()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "budget_defaults.yaml"
            with patch(
                "amiagi.interfaces.web.routes.budget_routes._BUDGET_DEFAULTS_PATH",
                config_path,
            ):
                client = _make_app(budget_manager=bm)
                r = client.put(
                    "/api/budget/quotas",
                    json={"agents": {"kastor": {"limit_usd": 20.0}}},
                )
                assert r.status_code == 200
                data = r.json()
                assert data["config"]["agents"]["kastor"]["limit_usd"] == 20.0


# ── POST /api/budget/reset ───────────────────────────────────

class TestBudgetReset:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_reset_session(self, _mock_log) -> None:
        bm = _mock_budget_manager()
        client = _make_app(budget_manager=bm)
        r = client.post("/api/budget/reset", json={"scope": "session"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["target"] == "session"
        bm.reset_all.assert_called_once()

    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_reset_agent(self, _mock_log) -> None:
        bm = _mock_budget_manager()
        client = _make_app(budget_manager=bm)
        r = client.post("/api/budget/reset", json={"agent_id": "kastor"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["target"] == "kastor"
        bm.reset_agent.assert_called_once_with("kastor")

    def test_reset_no_budget_manager(self) -> None:
        client = _make_app()
        r = client.post("/api/budget/reset", json={"scope": "session"})
        assert r.status_code == 503

    def test_reset_missing_scope_and_agent(self) -> None:
        bm = _mock_budget_manager()
        client = _make_app(budget_manager=bm)
        r = client.post("/api/budget/reset", json={})
        assert r.status_code == 400
