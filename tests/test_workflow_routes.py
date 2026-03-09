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


class _FakeHub:
    def __init__(self):
        self.events = []

    async def broadcast(self, name, payload):
        self.events.append((name, payload))


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

    def test_create_from_yaml_body(self) -> None:
        client = TestClient(_make_app())
        yaml_body = """
nodes:
  - node_id: analyze
    node_type: execute
    label: Analyze Code
    agent_role: executor
  - node_id: review
    node_type: gate
    label: Human Review
    depends_on: [analyze]
"""
        r = client.post(
            "/api/workflows",
            json={
                "name": "yaml-wf",
                "description": "from yaml",
                "yaml_body": yaml_body,
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["definition"]["name"] == "yaml-wf"
        assert len(data["definition"]["nodes"]) == 2
        assert data["definition"]["nodes"][1]["depends_on"] == ["analyze"]

    def test_invalid_yaml_body_returns_400(self) -> None:
        client = TestClient(_make_app())
        r = client.post(
            "/api/workflows",
            json={
                "name": "bad-yaml",
                "yaml_body": "nodes: [",
            },
        )
        assert r.status_code == 400


class TestWorkflowFrontendContract:
    def test_workflows_js_sends_yaml_body_instead_of_empty_nodes(self) -> None:
        from pathlib import Path

        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "workflows.js"
        content = js.read_text(encoding="utf-8")
        assert "yaml_body: yamlBody" in content
        assert "nodes: []" not in content

    def test_workflows_js_recognizes_waiting_approval_gate_status(self) -> None:
        from pathlib import Path

        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "workflows.js"
        content = js.read_text(encoding="utf-8")
        assert "waiting_approval" in content

    def test_workflows_js_contains_edit_clone_and_live_preview_hooks(self) -> None:
        from pathlib import Path

        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "workflows.js"
        content = js.read_text(encoding="utf-8")
        assert "data-action=\"edit\"" in content
        assert "data-action=\"clone\"" in content
        assert "workflow-editor-preview" in content
        assert "updateWorkflowPreview" in content

    def test_workflows_js_contains_contract_error_feedback_helpers(self) -> None:
        from pathlib import Path

        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "workflows.js"
        content = js.read_text(encoding="utf-8")
        assert "responseErrorMessage" in content
        assert "notify(await responseErrorMessage(res, \"Clone failed\")" in content
        assert "Gate approval failed" in content
        assert "Run ${action} failed" in content


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


class TestUpdateAndCloneWorkflowDefinition:
    def test_update_definition_ok(self) -> None:
        client = TestClient(_make_app())
        create = client.post("/api/workflows", json=_sample_def_body())
        wf_id = create.json()["id"]

        r = client.put(
            f"/api/workflows/{wf_id}",
            json={
                "name": "updated-wf",
                "nodes": [
                    {
                        "node_id": "n1",
                        "node_type": "execute",
                        "label": "Step 1",
                        "depends_on": [],
                        "config": {"progress_current": 3, "progress_total": 5},
                    }
                ],
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["definition"]["name"] == "updated-wf"
        assert data["definition"]["nodes"][0]["progress"] == "3/5"

    def test_clone_definition_creates_copy(self) -> None:
        client = TestClient(_make_app())
        create = client.post("/api/workflows", json=_sample_def_body())
        wf_id = create.json()["id"]

        r = client.post(f"/api/workflows/{wf_id}/clone", json={})

        assert r.status_code == 201
        payload = r.json()
        assert payload["definition"]["name"].endswith("(copy)")
        listed = client.get("/api/workflows").json()["definitions"]
        assert len(listed) == 2

    def test_update_definition_missing_returns_404(self) -> None:
        client = TestClient(_make_app())
        r = client.put("/api/workflows/missing", json=_sample_def_body())
        assert r.status_code == 404


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
        app = _make_app(engine)
        app.state.event_hub = _FakeHub()
        client = TestClient(app)
        r = client.post("/api/workflow-runs/r1/pause")
        assert r.status_code == 200
        assert r.json()["run"]["status"] == "paused"
        assert app.state.event_hub.events[-1][0] == "workflow.paused"

    def test_resume(self) -> None:
        engine = _FakeEngine()
        engine._runs["r1"] = _FakeRun(run_id="r1")
        app = _make_app(engine)
        app.state.event_hub = _FakeHub()
        client = TestClient(app)
        client.post("/api/workflow-runs/r1/pause")
        r = client.post("/api/workflow-runs/r1/resume")
        assert r.status_code == 200
        assert r.json()["run"]["status"] == "running"
        assert app.state.event_hub.events[-1][0] == "workflow.resumed"

    def test_run_not_found(self) -> None:
        engine = _FakeEngine()
        client = TestClient(_make_app(engine))
        r = client.post("/api/workflow-runs/nonexistent/pause")
        assert r.status_code == 404

    def test_abort(self) -> None:
        engine = _FakeEngine()
        engine._runs["r1"] = _FakeRun(run_id="r1")
        app = _make_app(engine)
        app.state.event_hub = _FakeHub()
        client = TestClient(app)
        r = client.post("/api/workflow-runs/r1/abort")
        assert r.status_code == 200
        assert r.json()["run"]["status"] == "failed"
        assert app.state.event_hub.events[-1][0] == "workflow.aborted"
