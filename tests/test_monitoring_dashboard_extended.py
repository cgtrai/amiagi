from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.alert_manager import Alert, AlertSeverity
from amiagi.infrastructure.trace_viewer import Span, Trace
from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceRecord
from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes


class _FakePerformanceTracker:
    def __init__(self, records: list[PerformanceRecord]) -> None:
        self._records = records

    async def query(self, *, agent_role=None, model=None, since=None, until=None, limit=100):
        def _to_dt(value: str | None):
            if not value:
                return None
            return datetime.fromisoformat(value.replace('Z', '+00:00'))

        since_dt = _to_dt(since)
        until_dt = _to_dt(until)
        items = self._records
        if agent_role:
            items = [item for item in items if item.agent_role == agent_role]
        if model:
            items = [item for item in items if item.model == model]
        if since_dt:
            items = [item for item in items if item.created_at and item.created_at >= since_dt]
        if until_dt:
            items = [item for item in items if item.created_at and item.created_at <= until_dt]
        items = sorted(items, key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:limit]

    async def summary(self, *, agent_role=None, model=None):
        rows = await self.query(agent_role=agent_role, model=model, limit=500)
        total = len(rows)
        duration_values = [row.duration_ms for row in rows if row.duration_ms is not None]
        return {
            "total": total,
            "avg_duration_ms": (sum(duration_values) / len(duration_values)) if duration_values else None,
            "p50_ms": duration_values[0] if duration_values else None,
            "p95_ms": max(duration_values) if duration_values else None,
            "success_rate": (sum(1 for row in rows if row.success) / total) if total else None,
            "total_tokens_in": sum(row.tokens_in for row in rows),
            "total_tokens_out": sum(row.tokens_out for row in rows),
            "total_cost_usd": sum(row.cost_usd for row in rows),
        }


class _FakeSessionRecorder:
    async def list_sessions(self, *, limit=50, agent_id=None):
        sessions = [
            {
                "session_id": "session-1",
                "event_count": 4,
                "agent_id": "kastor",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        if agent_id:
            sessions = [session for session in sessions if session["agent_id"] == agent_id]
        return sessions[:limit]

    async def get_session_events(self, session_id, limit=5000):
        return []


class _FakeAlertManager:
    def recent_alerts(self, last_n=50):
        now = datetime.now(timezone.utc).timestamp()
        return [
            Alert(rule_name="budget", message="Budget threshold exceeded", severity=AlertSeverity.WARNING, timestamp=now),
        ][:last_n]


class _FakeTraceViewer:
    def __init__(self) -> None:
        root = Span(span_id="span-root", trace_id="trace-1", operation="agent.ask", agent_id="kastor")
        root.finish(status="completed")
        child = Span(span_id="span-child", trace_id="trace-1", parent_span_id="span-root", operation="tool.search", agent_id="kastor")
        child.finish(status="completed")
        trace = Trace(trace_id="trace-1", root_span_id="span-root", metadata={"agent_role": "kastor", "model": "gpt-4", "status": "completed"})
        trace.add_span(root)
        trace.add_span(child)
        self._trace = trace

    def list_traces(self, *, limit=50):
        return [
            {
                "trace_id": "trace-1",
                "span_count": 2,
                "duration_ms": 42.0,
                "is_complete": True,
                "status": "completed",
            }
        ][:limit]

    def get_trace(self, trace_id: str):
        return self._trace if trace_id == "trace-1" else None

    def load_trace(self, trace_id: str):
        return self.get_trace(trace_id)


class _FakeUserSettingsRepo:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    async def get_for_user(self, user_id: str) -> dict:
        return dict(self._store.get(user_id, {}))

    async def save_for_user(self, user_id: str, settings: dict) -> dict:
        self._store[user_id] = dict(settings)
        return dict(self._store[user_id])


def _make_client() -> TestClient:
    now = datetime.now(timezone.utc)
    app = Starlette(routes=list(monitoring_routes))
    app.state.performance_tracker = _FakePerformanceTracker([
        PerformanceRecord(id=1, agent_role="kastor", model="gpt-4", task_type="code", duration_ms=210, success=True, tokens_in=120, tokens_out=340, cost_usd=0.012, created_at=now - timedelta(hours=1)),
        PerformanceRecord(id=2, agent_role="kastor", model="gpt-4", task_type="code", duration_ms=310, success=False, tokens_in=90, tokens_out=120, cost_usd=0.007, created_at=now - timedelta(hours=2)),
        PerformanceRecord(id=3, agent_role="polluks", model="qwen3", task_type="review", duration_ms=150, success=True, tokens_in=40, tokens_out=60, cost_usd=0.003, created_at=now - timedelta(days=2)),
    ])
    app.state.session_recorder = _FakeSessionRecorder()
    app.state.alert_manager = _FakeAlertManager()
    app.state.trace_viewer = _FakeTraceViewer()
    app.state.user_settings_repo = _FakeUserSettingsRepo()
    app.state.user = SimpleNamespace(user_id="user-1")
    return TestClient(app, raise_server_exceptions=False)


def test_monitoring_routes_include_new_dashboard_endpoints() -> None:
    paths = {route.path for route in monitoring_routes}
    assert "/api/monitoring/summary" in paths
    assert "/api/monitoring/layout" in paths
    assert "/api/metrics/export" in paths
    assert "/api/traces/{id}/tree" in paths


def test_monitoring_layout_roundtrip_persists_panel_order() -> None:
    client = _make_client()

    response = client.get("/api/monitoring/layout")

    assert response.status_code == 200
    default_order = response.json()["layout"]["panel_order"]
    assert "traces" in default_order
    assert "event-ticker" in default_order

    updated = client.put("/api/monitoring/layout", json={"panel_order": ["traces", "event-ticker", "alerts"]})

    assert updated.status_code == 200
    order = updated.json()["layout"]["panel_order"]
    assert order[:3] == ["traces", "event-ticker", "alerts"]
    assert sorted(order) == sorted(default_order)


def test_monitoring_summary_returns_cards_and_comparison() -> None:
    client = _make_client()

    response = client.get("/api/monitoring/summary?window=24h&agent=kastor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cards"]
    assert payload["cards"][0]["key"] == "tasks"
    assert payload["comparison"]
    assert payload["comparison"][0]["agent_role"] == "kastor"
    assert payload["totals"]["errors"] == 1.0


def test_traces_list_and_tree_use_trace_viewer() -> None:
    client = _make_client()

    response = client.get("/api/traces?agent_id=kastor")
    tree_response = client.get("/api/traces/trace-1/tree")

    assert response.status_code == 200
    traces = response.json()["traces"]
    assert traces[0]["trace_id"] == "trace-1"
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["tree"]["span_id"] == "span-root"
    assert tree_payload["timeline"][0]["span_id"] == "span-root"


def test_metrics_export_supports_json_and_csv() -> None:
    client = _make_client()

    json_response = client.get("/api/metrics/export?window=24h&agent=kastor")
    csv_response = client.get("/api/metrics/export?window=24h&agent=kastor&format=csv")

    assert json_response.status_code == 200
    assert json_response.json()["summary"]["cards"]
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert "trace_id,agent_role,model,status,duration_ms" in csv_response.text


def test_metrics_template_contains_trace_viewer_and_export_controls() -> None:
    template = Path("src/amiagi/interfaces/web/templates/metrics.html").read_text(encoding="utf-8")

    assert 'id="comparison-table"' in template
    assert 'id="trace-detail"' in template
    assert 'id="btn-customize-layout"' in template
    assert 'id="event-ticker-list"' in template
    assert 'data-panel-id="traces"' in template
    assert 'id="btn-export-metrics"' in template
    assert '/api/monitoring/summary' in template
    assert '/api/monitoring/layout' in template
    assert '/api/metrics/export' in template
    assert '/api/traces/' in template
