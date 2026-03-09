"""Tests for Eval / A/B / Benchmark routes — Sprint P3."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.eval_runner import EvalScenario
from amiagi.application.eval_runner import EvalRunResult
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


class _FakeEvalRunner:
    def __init__(self):
        self._pass_threshold = 50.0


class _Baseline:
    def __init__(self, score: float):
        self.aggregate_score = score


class _FakeBenchmarkSuite:
    def load_all(self):
        pass

    def list_categories(self):
        return ["code_gen", "reasoning"]

    def get_scenarios(self, name):
        return [EvalScenario(scenario_id="s1", prompt="test", expected_keywords=["ok"])]


class _FakeRuntime:
    def ask(self, prompt):
        return f"ok: {prompt}"


class _FakeRouterEngine:
    def __init__(self, agent_ids: list[str]):
        self._runtimes = {agent_id: _FakeRuntime() for agent_id in agent_ids}


class _FakeWebAdapter:
    def __init__(self, agent_ids: list[str]):
        self.router_engine = _FakeRouterEngine(agent_ids)


class _FakeABRunner:
    async def compare_async(self, agent_a_id, agent_a_fn, agent_b_id, agent_b_fn, scenarios):
        import time

        return type(
            "_Result",
            (),
            {
                "agent_a_id": agent_a_id,
                "agent_b_id": agent_b_id,
                "rubric_name": "default",
                "scenarios_count": len(scenarios),
                "a_wins": len(scenarios),
                "b_wins": 0,
                "ties": 0,
                "a_aggregate": 100.0,
                "b_aggregate": 0.0,
                "score_delta": 100.0,
                "started_at": time.time(),
                "finished_at": time.time(),
                "per_scenario": [],
            },
        )()


def _make_runtime_app(**kwargs) -> Starlette:
    defaults = {
        "eval_runner": _FakeEvalRunner(),
        "benchmark_suite": _FakeBenchmarkSuite(),
        "web_adapter": _FakeWebAdapter(["agent-1", "A", "B", "model_a", "model_b"]),
        "ab_test_runner": _FakeABRunner(),
    }
    defaults.update(kwargs)
    return _make_app(**defaults)


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


class TestRunEvaluation:
    def test_persists_selected_rubric_and_config(self) -> None:
        client = TestClient(_make_runtime_app())

        response = client.post(
            "/api/evaluations/run",
            json={
                "agent_id": "agent-1",
                "suite": "code_gen",
                "scorer": "llm_judge",
                "rubric": "code_quality",
                "label": "smoke",
            },
        )

        assert response.status_code == 201
        run_id = response.json()["id"]
        run = client.get(f"/api/evaluations/{run_id}").json()["run"]
        assert run["rubric_name"] == "code_quality"
        assert run["scorer"] == "llm_judge"
        assert run["config"]["rubric"]["name"] == "code_quality"
        assert run["config"]["scorer"] == "llm_judge"
        assert run["config"]["suite"] == "code_gen"
        assert run["config"]["label"] == "smoke"

    def test_rejects_invalid_custom_rubric_json(self) -> None:
        client = TestClient(_make_runtime_app())

        response = client.post(
            "/api/evaluations/run",
            json={
                "agent_id": "agent-1",
                "suite": "code_gen",
                "rubric": "custom",
                "custom_rubric": "{not-json}",
            },
        )

        assert response.status_code == 400

    def test_accepts_custom_rubric_and_persists_config_payload(self) -> None:
        client = TestClient(_make_runtime_app())

        response = client.post(
            "/api/evaluations/run",
            json={
                "agent_id": "agent-1",
                "suite": "code_gen",
                "rubric": "custom",
                "custom_rubric": '{"name":"custom","criteria":[{"name":"accuracy","weight":1.0,"max_score":5.0}]}',
            },
        )

        assert response.status_code == 201
        run_id = response.json()["id"]
        run = client.get(f"/api/evaluations/{run_id}").json()["run"]
        assert run["rubric_name"] == "custom"
        assert run["config"]["rubric"]["name"] == "custom"
        assert run["config"]["rubric"]["criteria"][0]["name"] == "accuracy"

    def test_rejects_when_runtime_is_unavailable_instead_of_creating_dead_pending_run(self) -> None:
        repo = _FakeEvalRepo()
        client = TestClient(_make_app(eval_repo=repo, eval_runner=_FakeEvalRunner(), benchmark_suite=_FakeBenchmarkSuite()))

        response = client.post(
            "/api/evaluations/run",
            json={"agent_id": "agent-1", "suite": "code_gen"},
        )

        assert response.status_code == 409
        data = response.json()
        assert data["error"] == "eval_run_not_supported"
        assert repo._runs == {}


class TestAbCampaigns:
    def test_list_empty(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/evaluations/ab-tests")
        assert r.status_code == 200
        assert r.json()["campaigns"] == []

    def test_create_ab(self) -> None:
        client = TestClient(_make_runtime_app())
        r = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "model_a", "agent_b_id": "model_b", "suite": "code_gen"},
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

    def test_create_ab_requires_suite(self) -> None:
        client = TestClient(_make_runtime_app())
        r = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B"},
        )
        assert r.status_code == 400
        assert r.json()["error"] == "suite is required"

    def test_create_ab_rejects_when_runtime_is_unavailable(self) -> None:
        repo = _FakeEvalRepo()
        client = TestClient(_make_app(eval_repo=repo, benchmark_suite=_FakeBenchmarkSuite(), ab_test_runner=_FakeABRunner()))
        r = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B", "suite": "code_gen"},
        )
        assert r.status_code == 409
        data = r.json()
        assert data["error"] == "ab_test_not_supported"
        assert repo._campaigns == {}

    def test_list_after_create(self) -> None:
        client = TestClient(_make_runtime_app())
        client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B", "suite": "code_gen"},
        )
        r = client.get("/api/evaluations/ab-tests")
        assert r.json()["total"] == 1

    def test_pause_ab(self) -> None:
        client = TestClient(_make_runtime_app())
        create = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B", "suite": "code_gen"},
        )
        cid = create.json()["id"]
        r = client.put(f"/api/evaluations/ab-tests/{cid}/pause")
        assert r.status_code == 200
        assert r.json()["status"] == "paused"

    def test_stop_ab(self) -> None:
        client = TestClient(_make_runtime_app())
        create = client.post(
            "/api/evaluations/ab-tests",
            json={"agent_a_id": "A", "agent_b_id": "B", "suite": "code_gen"},
        )
        cid = create.json()["id"]
        r = client.put(f"/api/evaluations/ab-tests/{cid}/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_pause_not_found(self) -> None:
        client = TestClient(_make_app())
        r = client.put("/api/evaluations/ab-tests/nonexistent/pause")
        assert r.status_code == 404


class TestEvalListMetadata:
    def test_list_includes_baseline_score_when_available(self) -> None:
        repo = _FakeEvalRepo()
        detector = _FakeRegressionDetector()
        detector._baselines["agent-1"] = _Baseline(82.5)
        repo._runs["run-1"] = {
            "id": "run-1",
            "agent_id": "agent-1",
            "status": "completed",
            "aggregate_score": 79.0,
            "started_at": 10,
        }
        client = TestClient(_make_app(eval_repo=repo, regression_detector=detector))

        response = client.get("/api/evaluations")

        assert response.status_code == 200
        run = response.json()["runs"][0]
        assert run["baseline_score"] == 82.5


class TestEvaluationsPageAssets:
    def test_evaluations_js_uses_explicit_notifications_for_run_and_ab_actions(self) -> None:
        script = Path("src/amiagi/interfaces/web/static/js/evaluations.js").read_text(encoding="utf-8")

        assert "function notify(message, level)" in script
        assert 'notify("Evaluation started", "success")' in script
        assert 'notify("A/B test started", "success")' in script
        assert 'notify(await responseErrorMessage(res, "Failed to start evaluation"), "error")' in script
        assert 'notify(await responseErrorMessage(res, "Failed to start A/B test"), "error")' in script
        assert "alert(" not in script
