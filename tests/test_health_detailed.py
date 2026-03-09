"""Tests for health_detailed endpoint — P14 Health Diagnostics."""

from __future__ import annotations

import json
import time
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from amiagi.interfaces.web.routes.health_routes import health, health_detailed, health_rate_limits


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


def _make_app(**state_attrs) -> Starlette:
    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/health/detailed", health_detailed, methods=["GET"]),
        Route("/api/health/rate-limits", health_rate_limits, methods=["GET"]),
    ])
    for k, v in state_attrs.items():
        setattr(app.state, k, v)
    return app


class TestHealthBasic:
    def test_health_ok(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestHealthDetailed:
    def test_returns_version(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/health/detailed")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert "status" in data

    def test_uptime(self) -> None:
        client = TestClient(_make_app(_startup_time=time.time() - 120))
        r = client.get("/health/detailed")
        data = r.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 100

    def test_db_pool_present(self) -> None:
        pool = MagicMock()
        pool.get_size.return_value = 10
        pool.get_idle_size.return_value = 8
        pool.get_min_size.return_value = 2
        pool.get_max_size.return_value = 20
        client = TestClient(_make_app(db_pool=pool))
        r = client.get("/health/detailed")
        data = r.json()
        assert data["db_pool"]["size"] == 10
        assert data["db_pool"]["free"] == 8

    def test_db_pool_absent_degraded(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/health/detailed")
        data = r.json()
        assert data["db_pool"] is None
        assert data["status"] == "degraded"

    def test_disk_usage(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/health/detailed")
        data = r.json()
        assert "disk" in data

    def test_agent_counts(self) -> None:
        agent1 = MagicMock()
        agent1.state = MagicMock()
        agent1.state.value = "idle"
        agent2 = MagicMock()
        agent2.state = MagicMock()
        agent2.state.value = "working"
        registry = MagicMock()
        registry.list_all.return_value = [agent1, agent2]
        client = TestClient(_make_app(agent_registry=registry))
        r = client.get("/health/detailed")
        data = r.json()
        assert data["agents"]["total"] == 2
        assert data["agents"]["idle"] == 1
        assert data["agents"]["working"] == 1

    def test_ollama_offline(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/health/detailed")
        data = r.json()
        assert "ollama" in data

    def test_extended_system_info_fields_are_present(self) -> None:
        task_running = MagicMock(status="running")
        task_waiting = MagicMock(status="pending")
        task_queue = MagicMock()
        task_queue.list_all.return_value = [task_running, task_waiting]

        perf = MagicMock()
        perf.queries_per_minute.return_value = 17

        client = TestClient(_make_app(task_queue=task_queue, performance_tracker=perf))
        r = client.get("/health/detailed")
        data = r.json()

        assert "python_version" in data
        assert data["tasks_running"] == 1
        assert data["queries_per_min"] == 17

    def test_rate_limits_endpoint_exists_and_lists_provider_rows(self) -> None:
        model_hub = MagicMock()
        model_hub.cloud_models = [
            {"provider": "openai", "name": "gpt"},
            {"provider": "openai", "name": "gpt-2"},
            {"provider": "anthropic", "name": "claude"},
        ]
        client = TestClient(_make_app(model_hub=model_hub))

        r = client.get("/api/health/rate-limits")
        assert r.status_code == 200
        data = r.json()
        assert [item["provider"] for item in data["rate_limits"]] == ["openai", "anthropic"]


class TestHealthDashboardTemplate:
    def test_health_template_exposes_alerts_and_extended_metrics(self) -> None:
        html = (_WEB_ROOT / "templates" / "health.html").read_text(encoding="utf-8")
        assert "btn-health-alerts" in html
        assert "metric-python" in html
        assert "metric-tasks-running" in html
        assert "metric-qpm" in html
        assert "/api/health/rate-limits" not in html  # fetched by JS, not hardcoded link

    def test_health_script_loads_rate_limits_and_extended_metrics(self) -> None:
        js = (_WEB_ROOT / "static" / "js" / "health.js").read_text(encoding="utf-8")
        assert "/api/health/rate-limits" in js
        assert 'setText("metric-python"' in js
        assert 'setText("metric-tasks-running"' in js
        assert 'setText("metric-qpm"' in js
