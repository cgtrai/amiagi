"""Tests for Model Hub extended routes (VRAM, benchmark, pull, cloud models)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes


# ── Helpers ──────────────────────────────────────────────────

def _make_app(**state_attrs) -> TestClient:
    app = Starlette(routes=list(model_hub_routes))
    for k, v in state_attrs.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def _mock_ollama(
    *,
    models: list | None = None,
    ps_models: list | None = None,
) -> MagicMock:
    """Build a fake OllamaClient with configurable responses."""
    oc = MagicMock()
    oc.base_url = "http://localhost:11434"
    # /api/tags
    oc.list_models.return_value = {"models": models or []}
    # /api/ps
    oc._get_json.return_value = {"models": ps_models or []}
    # generate / benchmark
    oc.generate.return_value = {
        "response": "Hello world",
        "total_duration": 2_000_000_000,
        "eval_count": 20,
        "eval_duration": 1_500_000_000,
    }
    return oc


def _mock_vram_scheduler(total_mb: int = 8192) -> MagicMock:
    vs = MagicMock()
    vs.total_vram_mb = total_mb
    return vs


_LOG_ACTION = "amiagi.interfaces.web.audit.log_helpers.log_action"


# ── GET /api/models/local ────────────────────────────────────

class TestLocalModels:
    @patch("amiagi.interfaces.web.routes.model_hub_routes.subprocess")
    def test_returns_models_list(self, mock_sub) -> None:
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="NAME          ID          SIZE    MODIFIED\nllama3:8b    abc123    4.7 GB   2 days ago\n",
        )
        client = _make_app()
        r = client.get("/api/models/local")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["models"][0]["name"] == "llama3:8b"

    @patch("amiagi.interfaces.web.routes.model_hub_routes.subprocess")
    def test_no_ollama_cli_fallback(self, mock_sub) -> None:
        mock_sub.run.side_effect = FileNotFoundError
        client = _make_app()
        r = client.get("/api/models/local")
        assert r.status_code == 200
        data = r.json()
        assert data["models"] == []


# ── GET /api/models/vram ─────────────────────────────────────

class TestVram:
    def test_vram_returns_running_models(self) -> None:
        ps = [{"name": "llama3:8b", "size": 4_000_000_000, "size_vram": 3_500_000_000}]
        oc = _mock_ollama(ps_models=ps)
        vs = _mock_vram_scheduler(total_mb=8192)
        client = _make_app(ollama_client=oc, vram_scheduler=vs)
        r = client.get("/api/models/vram")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["name"] == "llama3:8b"
        assert data["models"][0]["size_vram"] == 3_500_000_000
        # gpu_total should come from VRAMScheduler (8192 MB → bytes)
        assert data["gpu_total"] == 8192 * 1024 * 1024
        assert data["gpu_used"] == 3_500_000_000

    def test_vram_no_ollama_returns_503(self) -> None:
        client = _make_app()
        r = client.get("/api/models/vram")
        assert r.status_code == 503

    def test_vram_no_running_models(self) -> None:
        oc = _mock_ollama(ps_models=[])
        client = _make_app(ollama_client=oc)
        r = client.get("/api/models/vram")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["models"] == []
        assert data["gpu_total"] == 0

    def test_vram_scheduler_absent_returns_zero_total(self) -> None:
        ps = [{"name": "qwen3:14b", "size": 8_000_000_000}]
        oc = _mock_ollama(ps_models=ps)
        client = _make_app(ollama_client=oc)
        r = client.get("/api/models/vram")
        data = r.json()
        assert data["gpu_total"] == 0
        assert data["gpu_used"] == 8_000_000_000


# ── POST /api/models/benchmark ───────────────────────────────

class TestBenchmark:
    def test_benchmark_success(self) -> None:
        oc = _mock_ollama()
        oc._post_json.return_value = {
            "response": "Hello!",
            "total_duration": 2_000_000_000,
            "eval_count": 20,
            "eval_duration": 1_500_000_000,
        }
        client = _make_app(ollama_client=oc)
        r = client.post(
            "/api/models/benchmark",
            json={"model": "llama3:8b", "prompt": "Hello"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "tokens_per_second" in data
        assert data["tokens_per_second"] > 0

    def test_benchmark_missing_model_returns_400(self) -> None:
        oc = _mock_ollama()
        client = _make_app(ollama_client=oc)
        r = client.post("/api/models/benchmark", json={"model": ""})
        assert r.status_code == 400

    def test_benchmark_no_ollama_returns_503(self) -> None:
        client = _make_app()
        r = client.post("/api/models/benchmark", json={"model": "llama3:8b"})
        assert r.status_code == 503


# ── DELETE /api/models/local/{name} ──────────────────────────

class TestDeleteLocal:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    @patch("urllib.request.urlopen")
    def test_delete_success(self, mock_urlopen, _mock_log) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        oc = _mock_ollama()
        client = _make_app(ollama_client=oc)
        r = client.delete("/api/models/local/llama3%3A8b")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_delete_no_ollama(self) -> None:
        client = _make_app()
        r = client.delete("/api/models/local/llama3%3A8b")
        assert r.status_code == 503


# ── Cloud models CRUD ────────────────────────────────────────

class TestCloudModels:
    def test_list_cloud_models(self) -> None:
        models = [{"provider": "openai", "model": "gpt-4", "display_name": "GPT-4"}]
        with patch(
            "amiagi.interfaces.web.routes.model_hub_routes._read_cloud_config",
            return_value=models,
        ):
            client = _make_app()
            r = client.get("/api/models/cloud")
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert len(data["models"]) == 1

    def test_list_cloud_empty(self) -> None:
        with patch(
            "amiagi.interfaces.web.routes.model_hub_routes._read_cloud_config",
            return_value=[],
        ):
            client = _make_app()
            r = client.get("/api/models/cloud")
            assert r.status_code == 200
            assert r.json()["models"] == []

    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_add_cloud_model(self, _mock_log) -> None:
        with patch(
            "amiagi.interfaces.web.routes.model_hub_routes._read_cloud_config",
            return_value=[],
        ), patch(
            "amiagi.interfaces.web.routes.model_hub_routes._write_cloud_config",
        ):
            client = _make_app()
            r = client.post("/api/models/cloud", json={
                "provider": "openai",
                "model": "gpt-4",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test1234567890",
                "display_name": "GPT-4",
            })
            assert r.status_code == 200
            assert r.json()["ok"] is True

    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_delete_cloud_model(self, _mock_log) -> None:
        existing = [{"provider": "openai", "model": "gpt-4"}]
        with patch(
            "amiagi.interfaces.web.routes.model_hub_routes._read_cloud_config",
            return_value=existing,
        ), patch(
            "amiagi.interfaces.web.routes.model_hub_routes._write_cloud_config",
        ):
            client = _make_app()
            r = client.delete("/api/models/cloud/openai/gpt-4")
            assert r.status_code == 200
            assert r.json()["ok"] is True
