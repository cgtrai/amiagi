"""Tests for Eval / A/B / Benchmark routes — Sprint P3."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.eval_routes import eval_routes


# ── Helpers ─────────────────────────────────────────────────

class _FakeEvalRepo:
    """In-memory stand-in for EvalRepository.  Same async API."""

    def __init__(self):
        self._runs: dict = {}
        self._scenarios: dict = {}
        self._campaigns: dict = {}

    async def list_eval_runs(self, *, limit=50, offset=0, agent_id=None, suite=None):
        items = list(self._runs.values())
        if agent_id:
            items = [i for i in items if i.get("agent_id") == agent_id]
        if suite:
            items = [i for i in items if i.get("suite") == suite]
        total = len(items)
        items = items[offset:offset + limit]
        return items, total

    async def get_eval_run(self, run_id):
        return self._runs.get(run_id)

    async def upsert_eval_run(self, entry):
        self._runs[entry["id"]] = entry

    async def upsert_scenarios(self, run_id, scenarios):
        self._scenarios[run_id] = scenarios

    async def get_scenarios(self, run_id):
        return self._scenarios.get(run_id, [])

    async def list_ab_campaigns(self):
        return list(self._campaigns.values())

    async def get_ab_campaign(self, campaign_id):
        return self._campaigns.get(campaign_id)

    async def upsert_ab_campaign(self, entry):
        self._campaigns[entry["id"]] = entry

    async def update_ab_status(self, campaign_id, status):
        c = self._campaigns.get(campaign_id)
        if c is None:
            return None
        c["status"] = status
        if status == "completed":
            import time
            c["finished_at"] = time.time()
        return c


def _make_app(**kwargs) -> Starlette:
    app = Starlette(routes=list(eval_routes))
    # Always provide a fake eval_repo unless overridden
    if "eval_repo" not in kwargs:
        kwargs["eval_repo"] = _FakeEvalRepo()
    for key, val in kwargs.items():
        setattr(app.state, key, val)
    return app


class _FakeRegressionDetector:
    def __init__(self):
        self._baselines: dict = {}
        self.threshold = 0.05

    def list_baselines(self):
        return list(self._baselines.keys())

    def load_baseline(self, name):
        return self._baselines.get(name)

    def save_baseline(self, result):
        return "/baselines/saved"

    def delete_baseline(self, name):
        return self._baselines.pop(name, None) is not None


class _FakeBenchmarkSuite:
    def load_all(self):
        pass

    def list_categories(self):
        return ["code_gen", "reasoning"]

    def get_scenarios(self, name):
        return [{"id": "s1", "input": "test"}]


# ── Tests ───────────────────────────────────────────────────

class TestListEvals:
    def test_empty(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations")
        assert r.status_code == 200
        data = r.json()
        assert data["runs"] == []
        assert data["total"] == 0


class TestRegressionsReport:
    def test_no_detector(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations/regressions")
        assert r.status_code == 200
        # Returns empty list, not 503
        assert r.json()["regressions"] == []

    def test_with_detector(self) -> None:
        detector = _FakeRegressionDetector()
        client = TestClient(_make_app(regression_detector=detector))
        r = client.get("/api/evaluations/regressions")
        assert r.status_code == 200
        assert r.json()["regressions"] == []


class TestBaselines:
    def test_no_detector(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations/baselines")
        assert r.status_code == 200
        assert r.json()["baselines"] == []

    def test_empty_baselines(self) -> None:
        detector = _FakeRegressionDetector()
        client = TestClient(_make_app(regression_detector=detector))
        r = client.get("/api/evaluations/baselines")
        assert r.status_code == 200
        assert r.json()["baselines"] == []


class TestSuites:
    def test_no_suite(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations/suites")
        assert r.status_code == 200
        assert r.json()["suites"] == []

    def test_list_suites(self) -> None:
        bsuite = _FakeBenchmarkSuite()
        client = TestClient(_make_app(benchmark_suite=bsuite))
        r = client.get("/api/evaluations/suites")
        data = r.json()
        assert data["total"] == 2
        names = [s["name"] for s in data["suites"]]
        assert "code_gen" in names


class TestAbCampaigns:
    def test_list_empty(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations/ab-tests")
        assert r.status_code == 200
        assert r.json()["campaigns"] == []

    def test_create_ab(self) -> None:
        client = TestClient(_make_app())
        r = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "model_a", "agent_b_id": "model_b", "suite": "test"},
        )
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert data["status"] == "pending"

    def test_create_ab_missing_agents(self) -> None:
        client = TestClient(_make_app())
        r = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A"},
        )
        assert r.status_code == 400

    def test_list_after_create(self) -> None:
        client = TestClient(_make_app())
        client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B"},
        )
        r = client.get("/api/evaluations/ab-tests")
        assert r.json()["total"] == 1

    def test_pause_ab(self) -> None:
        client = TestClient(_make_app())
        create = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B"},
        )
        cid = create.json()["id"]
        r = client.put(f"/api/evaluations/ab-tests/{cid}/pause")
        assert r.status_code == 200
        assert r.json()["status"] == "paused"

    def test_stop_ab(self) -> None:
        client = TestClient(_make_app())
        create = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B"},
        )
        cid = create.json()["id"]
        r = client.put(f"/api/evaluations/ab-tests/{cid}/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_pause_not_found(self) -> None:
        client = TestClient(_make_app())
        r = client.put("/api/evaluations/ab-tests/nonexistent/pause")
        assert r.status_code == 404
