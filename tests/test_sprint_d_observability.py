from __future__ import annotations

import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.alert_manager import Alert, AlertSeverity
from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes


class _FakeAlertManager:
    def __init__(self, alerts: list[Alert]) -> None:
        self._alerts = alerts

    def recent_alerts(self, last_n: int = 50) -> list[Alert]:
        return self._alerts[-last_n:]


def _make_client(alerts: list[Alert]) -> TestClient:
    app = Starlette(routes=list(monitoring_routes))
    app.state.alert_manager = _FakeAlertManager(alerts)
    return TestClient(app, raise_server_exceptions=False)


def test_alerts_route_is_registered() -> None:
    paths = {route.path for route in monitoring_routes}
    assert "/api/alerts" in paths


def test_api_alerts_respects_window_filter() -> None:
    now = time.time()
    client = _make_client([
        Alert(
            rule_name="old-budget",
            message="old",
            severity=AlertSeverity.WARNING,
            timestamp=now - 8 * 24 * 3600,
        ),
        Alert(
            rule_name="recent-budget",
            message="recent",
            severity=AlertSeverity.CRITICAL,
            timestamp=now - 30,
        ),
    ])

    response = client.get("/api/alerts?window=24h&limit=20")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["rule_name"] == "recent-budget"
    assert payload[0]["severity"] == "critical"


def test_metrics_template_contains_observability_sections() -> None:
    template = Path("src/amiagi/interfaces/web/templates/metrics.html").read_text(encoding="utf-8")

    assert 'id="metrics-window"' in template
    assert 'id="alerts-list"' in template
    assert 'id="sessions-list"' in template
    assert 'id="performance-summary"' in template
    assert '/api/alerts' in template
    assert '/api/sessions' in template
    assert '/api/performance/summary' in template
    assert '/sessions?session_id=' in template
