from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


def _make_client(**state_attrs) -> TestClient:
    app = Starlette(routes=list(model_hub_routes))
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_model_hub_routes_include_queue_unload_and_performance() -> None:
    paths = {route.path: (route.methods or set()) for route in model_hub_routes}

    assert "/api/models/queue" in paths
    assert "/api/models/{name:path}/unload" in paths
    assert "/api/models/{name:path}/performance" in paths
    assert "GET" in paths["/api/models/queue"]
    assert "POST" in paths["/api/models/{name:path}/unload"]
    assert "GET" in paths["/api/models/{name:path}/performance"]


def test_model_queue_route_serializes_waiting_entries() -> None:
    scheduler = SimpleNamespace(waiting_queue=[SimpleNamespace(model_name="llama3:8b", agent_id="agent-7", waiting_since="10s")])
    client = _make_client(vram_scheduler=scheduler)

    response = client.get("/api/models/queue")

    assert response.status_code == 200
    assert response.json()["queue"] == [{"model_name": "llama3:8b", "agent_id": "agent-7", "waiting_since": "10s"}]


def test_model_performance_route_filters_results(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "benchmarks.json").write_text(
        json.dumps([
            {"model": "llama3:8b", "tokens_per_second": 11.2},
            {"model": "qwen3:14b", "tokens_per_second": 8.4},
        ]),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    client = _make_client()

    response = client.get("/api/models/llama3%3A8b/performance")

    assert response.status_code == 200
    assert response.json() == {
        "model": "llama3:8b",
        "results": [{"model": "llama3:8b", "tokens_per_second": 11.2}],
    }


def test_model_hub_template_and_js_cover_context_vram_cost_editor_and_unload() -> None:
    html = (_WEB_ROOT / "templates" / "model_hub.html").read_text(encoding="utf-8")
    js = (_WEB_ROOT / "static" / "js" / "model_hub.js").read_text(encoding="utf-8")

    assert "context_length" in js
    assert "vram_mb" in js
    assert "cost_per_1k" in js
    assert 'id="model-config-json"' in html
    assert 'id="btn-save-config"' in html
    assert 'id="model-queue"' in html
    assert "/api/models/queue" in js
    assert "/api/models/" in js and "/unload" in js
    assert '/models' in js
    assert 'value="google"' in html
    assert 'generativelanguage.googleapis.com/v1beta' in html
    assert 'google: "https://generativelanguage.googleapis.com/v1beta"' in js
    assert 'Pull complete' in js
    assert 'Pull complete ✓' not in js
    assert '✓ Connected' not in js
    assert 'Saved ✓' not in js
    assert 'notifyModelHub' in js
    assert 'responseErrorMessage' in js
    assert 'alert(' not in js
