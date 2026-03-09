from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.task_queue import TaskQueue
from amiagi.domain.agent import AgentDescriptor, AgentRole
from amiagi.domain.task import Task
from amiagi.interfaces.web.routes.agent_config_routes import agent_config_routes
from amiagi.interfaces.web.routes.api_routes import api_routes
from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes


_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


class _Registry:
    def __init__(self, agent: AgentDescriptor) -> None:
        self._agent = agent

    def get(self, agent_id: str):
        if agent_id == self._agent.agent_id:
            return self._agent
        return None


class _ActivityLogger:
    async def query(self, limit: int = 100):
        return [
            {
                "id": 1,
                "action": "agent.pause",
                "detail": {"agent_id": "polluks", "reason": "operator"},
                "created_at": None,
            },
            {
                "id": 2,
                "action": "task.create",
                "detail": {"assigned_agent_id": "polluks", "task_id": "t1"},
                "created_at": None,
            },
        ]


class _EvalRun:
    def to_dict(self):
        return {
            "agent_id": "polluks",
            "rubric_name": "smoke",
            "aggregate_score": 4.5,
            "passed": 3,
            "scenarios_count": 3,
        }


class _EvalRunner:
    def history(self, agent_id: str):
        return [_EvalRun()] if agent_id == "polluks" else []


def _make_app() -> Starlette:
    app = Starlette(routes=[*api_routes, *agent_config_routes])
    task_queue = TaskQueue()
    task = Task(task_id="t-1", title="Review fix", description="Check Sprint C", result="pending review")
    task.dependencies = ["dep-1", "dep-2"]
    task.metadata = {"origin": "operator", "severity": "medium"}
    task_queue.enqueue(task)

    agent = AgentDescriptor(
        agent_id="polluks",
        name="Polluks",
        role=AgentRole.EXECUTOR,
        model_name="llama3",
        model_backend="ollama",
        skills=["python", "review"],
        tools=["read_file", "apply_patch"],
        metadata={"permissions": ["workspace.read", "workspace.write"], "workspace": "shared"},
    )

    app.state.task_queue = task_queue
    app.state.agent_registry = _Registry(agent)
    app.state.activity_logger = _ActivityLogger()
    app.state.eval_runner = _EvalRunner()
    return app


def test_prompt_and_snippet_page_routes_exist() -> None:
    paths = [route.path for route in dashboard_routes]
    assert "/prompt-library" in paths
    assert "/prompts-library" in paths
    assert "/snippets-library" in paths


def test_prompt_template_exists_and_uses_prompt_api() -> None:
    html = (_ROOT / "templates" / "prompts.html").read_text(encoding="utf-8")
    assert "/prompts" in html
    assert "prompt-grid" in html
    assert "prompt-tag-filters" in html
    assert "sendPromptToAgent" in html


def test_snippets_template_exists_and_uses_snippet_api() -> None:
    html = (_ROOT / "templates" / "snippets.html").read_text(encoding="utf-8")
    assert "/snippets" in html
    assert "snippet-grid" in html
    assert "Ctrl+Shift+V" in html
    assert "/api/snippets/export?format=" in html
    assert "editSnippet" in html


def test_command_rail_links_to_productivity_pages() -> None:
    html = (_ROOT / "templates" / "partials" / "command_rail.html").read_text(encoding="utf-8")
    assert 'href="/prompt-library"' in html
    assert 'href="/snippets-library"' in html


def test_task_detail_route_returns_full_task_payload() -> None:
    client = TestClient(_make_app())
    response = client.get("/api/tasks/t-1")
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["task_id"] == "t-1"
    assert task["dependencies"] == ["dep-1", "dep-2"]
    assert task["result"] == "pending review"


def test_agent_drawer_routes_return_data() -> None:
    client = TestClient(_make_app())

    permissions = client.get("/api/agents/polluks/permissions")
    history = client.get("/api/agents/polluks/history")
    benchmarks = client.get("/api/agents/polluks/benchmarks")

    assert permissions.status_code == 200
    assert history.status_code == 200
    assert benchmarks.status_code == 200

    assert permissions.json()["permissions"] == ["workspace.read", "workspace.write"]
    assert history.json()["total"] >= 1
    assert benchmarks.json()["benchmarks"][0]["rubric_name"] == "smoke"


def test_agents_page_contains_drawer_tabs_and_actions() -> None:
    html = (_ROOT / "templates" / "agents.html").read_text(encoding="utf-8")
    assert "AGENT_DRAWER_TABS" in html
    assert "openAgentDrawer" in html
    assert "runAgentAction" in html


def test_tasks_page_contains_task_detail_drawer_logic() -> None:
    html = (_ROOT / "templates" / "tasks.html").read_text(encoding="utf-8")
    assert "openTaskDetailDrawer" in html
    assert "/api/tasks/" in html
    assert "tasks-timeline" in html
    assert "quick-create-title" in html
    assert "new WebSocket" in html
    assert "subtask-tree-node" in html
    assert "workflow-dag-shell" in html


def test_task_wizard_contains_template_parameterization_hooks() -> None:
    html = (_ROOT / "templates" / "task_wizard.html").read_text(encoding="utf-8")

    assert "tpl-params-form" in html
    assert "Rendered Workflow Steps" in html
    assert "/templates/stats" in html
    assert "/preview" in html
    assert "/execute" in html


def test_task_board_emits_selection_event() -> None:
    js = (_ROOT / "static" / "js" / "components" / "task-board.js").read_text(encoding="utf-8")
    assert "task:selected" in js
    assert "data-task-id" in js
    assert "task:moved" in js
    assert "data-drop-status" in js
    assert "draggable=\"true\"" in js
