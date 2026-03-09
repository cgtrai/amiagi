from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.budget_routes import budget_routes

_LOG_ACTION = "amiagi.interfaces.web.audit.log_helpers.log_action"


def _make_client(**state_attrs) -> TestClient:
    app = Starlette(routes=list(budget_routes))
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_budget_quotas_get_returns_defaults() -> None:
    client = _make_client()

    response = client.get("/api/budget/quotas")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["config"]["thresholds"]["warning_action"] == "notify"
    assert payload["config"]["thresholds"]["blocked_action"] == "block"


@patch(_LOG_ACTION, new_callable=AsyncMock)
def test_budget_quotas_update_persists_policy_actions(_mock_log) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "budget_defaults.yaml"
        bm = MagicMock()
        bm.set_session_budget = MagicMock()
        client = _make_client(budget_manager=bm)
        with patch("amiagi.interfaces.web.routes.budget_routes._BUDGET_DEFAULTS_PATH", config_path):
            response = client.put(
                "/api/budget/quotas",
                json={
                    "warning_action": "inbox",
                    "blocked_action": "pause",
                    "approval_threshold_usd": 12.5,
                },
            )

    assert response.status_code == 200
    payload = response.json()
    thresholds = payload["config"]["thresholds"]
    assert thresholds["warning_action"] == "inbox"
    assert thresholds["blocked_action"] == "pause"
    assert thresholds["approval_threshold_usd"] == 12.5


def test_budget_page_contains_advanced_action_controls() -> None:
    template = Path("src/amiagi/interfaces/web/templates/budget.html").read_text(encoding="utf-8")

    assert "quota-warning-action" in template
    assert "quota-blocked-action" in template
    assert "quota-approval-threshold" in template
    assert "budget-policy-summary" in template


def test_budget_js_uses_budget_quotas_endpoint_and_normalizes_objects() -> None:
    script = Path("src/amiagi/interfaces/web/static/js/budget.js").read_text(encoding="utf-8")

    assert "/api/budget/quotas" in script
    assert "mapFromObject" in script
    assert "populateQuotaForm" in script
