from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.task_queue import TaskQueue
from amiagi.domain.task import Task, TaskPriority, TaskStatus
from amiagi.interfaces.web.routes.api_routes import api_routes


class _FakeRun:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.status = "running"
        self.started_at = 123.0
        self.finished_at = None
        self.workflow = SimpleNamespace(
            name="Deploy Flow",
            description="Pipeline",
            nodes=[
                SimpleNamespace(
                    node_id="build",
                    node_type=SimpleNamespace(value="execute"),
                    label="Build",
                    description="Run build",
                    depends_on=[],
                    status=SimpleNamespace(value="completed"),
                    result="ok",
                )
            ],
        )


class _FakeWorkflowEngine:
    def __init__(self) -> None:
        self._run = _FakeRun()

    def get_run(self, run_id: str):
        return self._run if run_id == "run-1" else None

    def list_runs(self):
        return [self._run]


def _make_app() -> Starlette:
    app = Starlette(routes=list(api_routes))
    app.state.task_queue = TaskQueue()
    app.state.workflow_engine = _FakeWorkflowEngine()
    app.state.activity_logger = SimpleNamespace(log=AsyncMock(return_value=1))
    app.state.event_hub = SimpleNamespace(broadcast=AsyncMock())
    return app


def test_list_tasks_supports_search_and_since_filters() -> None:
    app = _make_app()
    old_task = Task(task_id="t-old", title="Legacy task", description="Old")
    old_task.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    new_task = Task(task_id="t-new", title="Deploy release", description="Ship it")
    app.state.task_queue.enqueue(old_task)
    app.state.task_queue.enqueue(new_task)

    client = TestClient(app)
    response = client.get("/api/tasks?q=deploy&since=" + datetime.now(timezone.utc).date().isoformat())

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["tasks"][0]["task_id"] == "t-new"


def test_create_task_falls_back_to_enqueue_when_queue_has_no_add() -> None:
    app = _make_app()
    client = TestClient(app)

    response = client.post("/api/tasks", json={"title": "Write docs", "priority": "high", "origin": "web"})

    assert response.status_code == 201
    task_id = response.json()["task_id"]
    stored = app.state.task_queue.get(task_id)
    assert stored is not None
    assert stored.title == "Write docs"
    assert stored.priority == TaskPriority.HIGH
    assert stored.metadata["origin"] == "web"


def test_decompose_endpoint_creates_subtasks() -> None:
    app = _make_app()
    parent = Task(task_id="parent", title="Big feature", description="Split me")
    app.state.task_queue.enqueue(parent)
    client = TestClient(app)

    response = client.post("/api/tasks/parent/decompose")

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] >= 1
    subtasks = client.get("/api/tasks/parent/subtasks").json()["subtasks"]
    assert subtasks
    assert subtasks[0]["parent_task_id"] == "parent"


def test_task_workflow_endpoint_resolves_run_from_metadata() -> None:
    app = _make_app()
    task = Task(task_id="t-flow", title="Deploy")
    task.metadata = {"workflow_run_id": "run-1"}
    app.state.task_queue.enqueue(task)
    client = TestClient(app)

    response = client.get("/api/tasks/t-flow/workflow")

    assert response.status_code == 200
    workflow = response.json()["workflow"]
    assert workflow["run_id"] == "run-1"
    assert workflow["workflow_name"] == "Deploy Flow"


def test_task_stats_include_average_completion_time() -> None:
    app = _make_app()
    task = Task(task_id="t-done", title="Done")
    task.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    task.completed_at = datetime.now(timezone.utc)
    task.status = TaskStatus.DONE
    app.state.task_queue.enqueue(task)
    client = TestClient(app)

    response = client.get("/api/tasks/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload["avg_completion_time_seconds"] is not None
    assert payload["avg_completion_time_label"]


def test_bulk_task_actions_cancel_and_reassign() -> None:
    app = _make_app()
    first = Task(task_id="t-1", title="One")
    second = Task(task_id="t-2", title="Two")
    app.state.task_queue.enqueue(first)
    app.state.task_queue.enqueue(second)
    client = TestClient(app)

    response = client.post("/api/tasks/bulk", json={"action": "reassign", "task_ids": ["t-1", "t-2"], "agent_id": "polluks"})

    assert response.status_code == 200
    assert app.state.task_queue.get("t-1").assigned_agent_id == "polluks"
    assert app.state.task_queue.get("t-2").assigned_agent_id == "polluks"

    cancel_response = client.post("/api/tasks/bulk", json={"action": "cancel", "task_ids": ["t-1", "t-2"]})
    assert cancel_response.status_code == 200
    assert app.state.task_queue.get("t-1").status == TaskStatus.CANCELLED
    assert app.state.task_queue.get("t-2").status == TaskStatus.CANCELLED


def test_create_task_broadcasts_live_event() -> None:
    app = _make_app()
    client = TestClient(app)

    response = client.post("/api/tasks", json={"title": "Queue live refresh", "priority": "high"})

    assert response.status_code == 201
    app.state.event_hub.broadcast.assert_awaited()
    event_name, payload = app.state.event_hub.broadcast.await_args.args
    assert event_name == "task.created"
    assert payload["task_id"] == response.json()["task_id"]
    assert payload["title"] == "Queue live refresh"
    assert payload["thread_owners"] == ["supervisor"]
    assert payload["message_type"] == "task.created"


def test_create_task_for_agent_routes_only_to_target_agent_screen() -> None:
    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/api/tasks",
        json={"title": "Queue live refresh", "priority": "high", "assigned_agent_id": "nova"},
    )

    assert response.status_code == 201
    event_name, payload = app.state.event_hub.broadcast.await_args.args
    assert event_name == "task.created"
    assert payload["thread_owners"] == ["agent:nova"]
    assert "supervisor" not in payload["thread_owners"]


def test_reassign_task_broadcasts_live_event() -> None:
    app = _make_app()
    task = Task(task_id="t-live", title="Reassign me")
    app.state.task_queue.enqueue(task)
    client = TestClient(app)

    response = client.post("/api/tasks/t-live/reassign", json={"agent_id": "kastor"})

    assert response.status_code == 200
    event_name, payload = app.state.event_hub.broadcast.await_args.args
    assert event_name == "task.reassigned"
    assert payload["task_id"] == "t-live"
    assert payload["agent_id"] == "kastor"
    assert payload["status"] == "pending"
    assert payload["thread_owners"] == ["agent:kastor"]


def test_move_task_updates_status_and_broadcasts_live_event() -> None:
    app = _make_app()
    task = Task(task_id="t-move", title="Move me")
    app.state.task_queue.enqueue(task)
    client = TestClient(app)

    response = client.post("/api/tasks/t-move/move", json={"status": "in_progress"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "in_progress"
    assert app.state.task_queue.get("t-move").status == TaskStatus.IN_PROGRESS
    event_name, event_payload = app.state.event_hub.broadcast.await_args.args
    assert event_name == "task.moved"
    assert event_payload["task_id"] == "t-move"
    assert event_payload["to_status"] == "in_progress"
    assert event_payload["thread_owners"] == ["supervisor"]


def test_move_task_to_done_routes_completion_report_to_supervisor_and_agent() -> None:
    app = _make_app()
    task = Task(task_id="t-done", title="Done me")
    task.assigned_agent_id = "nova"
    app.state.task_queue.enqueue(task)
    client = TestClient(app)

    response = client.post("/api/tasks/t-done/move", json={"status": "done"})

    assert response.status_code == 200
    event_name, event_payload = app.state.event_hub.broadcast.await_args.args
    assert event_name == "task.moved"
    assert event_payload["thread_owners"] == ["supervisor", "agent:nova"]


def test_tasks_template_contains_decompose_and_workflow_controls() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "amiagi" / "interfaces" / "web" / "templates"
    html = (root / "tasks.html").read_text(encoding="utf-8")
    assert "filter-search" in html
    assert "/decompose" in html
    assert "/workflow" in html
    assert "reassignTaskFromDrawer" in html
    assert "tasks-bulk-bar" in html
    assert "bulkUpdateTasks" in html
    assert "data-view=\"timeline\"" in html
    assert "quick-create-form" in html
    assert "tasks-live-label" in html
    assert "connectTaskEvents" in html
    assert "task-subtask-tree" in html
    assert "workflow-dag.js" in html
    assert "task-workflow-dag" in html
    assert "hydrateTaskDrawerVisuals" in html
    assert "tasks.drag_drop" in html
    assert "/move" in html
    assert "task:moved" in html
