"""Tests for Workflow Studio routes — Sprint P3."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.workflow_routes import workflow_routes
from amiagi.domain.workflow import WorkflowDefinition, WorkflowNode, NodeType


# ── Helpers ─────────────────────────────────────────────────

def _make_app(engine=None) -> Starlette:
    """Create test app with only workflow routes."""
    app = Starlette(routes=list(workflow_routes))
    if engine is not None:
        app.state.workflow_engine = engine
    return app


def _sample_def_body() -> dict:
    """A minimal valid workflow-definition JSON body."""
    return {
        "name": "test-wf",
        "description": "a test workflow",
        "nodes": [
            {
                "node_id": "n1",
                "node_type": "execute",
                "label": "Step 1",
                "description": "first step",
                "agent_role": "executor",
                "depends_on": [],
            }
        ],
    }


class _FakeRun:
    def __init__(self, run_id="r1", workflow=None, status="running"):
        self.run_id = run_id
        self.workflow = workflow or WorkflowDefinition.from_dict(_sample_def_body())
        self.status = status
        self.started_at = 1700000000
        self.finished_at = None
        self.is_terminal = False


class _FakeEngine:
    def __init__(self):
        self._runs: dict[str, _FakeRun] = {}

    def list_runs(self):
        return list(self._runs.values())

    def get_run(self, run_id):
        return self._runs.get(run_id)

    def start(self, workflow, run_id=None):
        r = _FakeRun(run_id=run_id or "r_new", workflow=workflow)
        self._runs[r.run_id] = r
        return r

    def approve_gate(self, run_id, node_id):
        return run_id in self._runs

    def pause(self, run_id):
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.status = "paused"
        return True

    def resume(self, run_id):
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.status = "running"
        return True


# ── Tests: Definition CRUD ──────────────────────────────────

class TestListWorkflowDefinitions:
    def test_empty(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/workflows")
        assert r.status_code == 200
        assert r.json()["definitions"] == []

    def test_after_create(self) -> None:
        client = TestClient(_make_app())
        client.post("/api/workflows", json=_sample_def_body())
        r = client.get("/api/workflows")
        defs = r.json()["definitions"]
        assert len(defs) == 1
        assert defs[0]["name"] == "test-wf"


class TestCreateWorkflowDefinition:
    def test_create_ok(self) -> None:
        client = TestClient(_make_app())
        r = client.post("/api/workflows", json=_sample_def_body())
        assert r.status_code == 201
        data = r.json()
        assert "id" in data

    def test_missing_name(self) -> None:
        client = TestClient(_make_app())
        body = _sample_def_body()
        del body["name"]
        r = client.post("/api/workflows", json=body)
        assert r.status_code == 400

    def test_missing_nodes(self) -> None:
        client = TestClient(_make_app())
        r = client.post("/api/workflows", json={"name": "wf", "nodes": []})
        assert r.status_code == 400


class TestGetWorkflowDefinition:
    def test_not_found(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/workflows/nonexistent")
        assert r.status_code == 404

    def test_found(self) -> None:
        client = TestClient(_make_app())
        create = client.post("/api/workflows", json=_sample_def_body())
        wf_id = create.json()["id"]
        r = client.get(f"/api/workflows/{wf_id}")
        assert r.status_code == 200


class TestDeleteWorkflowDefinition:
    def test_delete(self) -> None:
        client = TestClient(_make_app())
        create = client.post("/api/workflows", json=_sample_def_body())
        wf_id = create.json()["id"]
        r = client.delete(f"/api/workflows/{wf_id}")
        assert r.status_code == 200
        r2 = client.get(f"/api/workflows/{wf_id}")
        assert r2.status_code == 404


# ── Tests: Runs ─────────────────────────────────────────────

class TestListWorkflowRuns:
    def test_no_engine(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/workflow-runs")
        assert r.status_code == 503

    def test_empty(self) -> None:
        client = TestClient(_make_app(_FakeEngine()))
        r = client.get("/api/workflow-runs")
        assert r.status_code == 200
        assert r.json()["runs"] == []


class TestWorkflowRunPauseResume:
    def test_pause(self) -> None:
        engine = _FakeEngine()
        engine._runs["r1"] = _FakeRun(run_id="r1")
        client = TestClient(_make_app(engine))
        r = client.post("/api/workflow-runs/r1/pause")
        assert r.status_code == 200

    def test_resume(self) -> None:
        engine = _FakeEngine()
        engine._runs["r1"] = _FakeRun(run_id="r1")
        client = TestClient(_make_app(engine))
        client.post("/api/workflow-runs/r1/pause")
        r = client.post("/api/workflow-runs/r1/resume")
        assert r.status_code == 200

    def test_run_not_found(self) -> None:
        engine = _FakeEngine()
        client = TestClient(_make_app(engine))
        r = client.post("/api/workflow-runs/nonexistent/pause")
        assert r.status_code == 404

    def test_abort(self) -> None:
        engine = _FakeEngine()
        engine._runs["r1"] = _FakeRun(run_id="r1")
        client = TestClient(_make_app(engine))
        r = client.post("/api/workflow-runs/r1/abort")
        assert r.status_code == 200
