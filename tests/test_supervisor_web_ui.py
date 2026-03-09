from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.domain.task import TaskStatus
from amiagi.interfaces.web.routes.system_routes import system_command_execute, system_commands, system_state


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


def _make_client(**state_attrs) -> TestClient:
    app = Starlette(routes=[Route("/api/system/state", system_state, methods=["GET"])])
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_system_state_includes_extended_supervisor_metrics_and_current_task() -> None:
    running_task = SimpleNamespace(
        task_id="task-1",
        title="Investigate regression",
        status=TaskStatus.IN_PROGRESS,
        assigned_agent_id="agent-1",
        progress_pct=80,
        steps_done=14,
        steps_total=18,
    )
    pending_task = SimpleNamespace(task_id="task-2", title="Queued", status=TaskStatus.PENDING, assigned_agent_id=None)
    task_queue = MagicMock()
    task_queue.pending_count.return_value = 2
    task_queue.total_count.return_value = 5
    task_queue.stats.return_value = {"in_progress": 1, "pending": 2}
    task_queue.list_all.return_value = [running_task, pending_task]

    registry = MagicMock()
    registry.list_all.return_value = [SimpleNamespace(state="working")]
    registry.get.return_value = SimpleNamespace(model_name="gpt-supervisor")

    budget_manager = SimpleNamespace(session_budget=SimpleNamespace(tokens_used=321, spent_usd=12.5))
    client = _make_client(
        task_queue=task_queue,
        agent_registry=registry,
        budget_manager=budget_manager,
        cycle_count=17,
        error_count=4,
    )

    response = client.get("/api/system/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle"] == 17
    assert payload["tokens_session"] == 321
    assert payload["cost_session"] == 12.5
    assert payload["error_count"] == 4
    assert payload["queue_length"] == 2
    assert payload["tasks"]["running"] == 1
    assert payload["current_task"] == {
        "task_id": "task-1",
        "title": "Investigate regression",
        "agent_id": "agent-1",
        "model_name": "gpt-supervisor",
        "progress_pct": 80,
        "steps_done": 14,
        "steps_total": 18,
    }


def test_supervisor_template_exposes_updated_state_cards_and_actions() -> None:
    html = (_WEB_ROOT / "templates" / "supervisor.html").read_text(encoding="utf-8")

    assert 'id="sv-cycle-count"' in html
    assert 'id="sv-tokens-count"' in html
    assert 'id="sv-session-cost"' in html
    assert 'id="sv-errors-count"' in html
    assert 'id="sv-queue-count"' in html
    assert "supervisor.current_task" in html
    assert "supervisor.new_prompt" in html
    assert 'id="btn-sv-commands"' in html
    assert "supervisor.commands" in html
    assert 'id="sv-stream-channel-filter"' in html
    assert 'id="sv-stream-level-filter"' in html
    assert 'id="btn-sv-stream-clear"' in html
    assert 'id="sv-stream-source"' in html
    assert "supervisor.queue" in html
    assert "supervisor.reset_session" in html


def test_supervisor_js_renders_extended_state_and_agent_models() -> None:
    js = (_WEB_ROOT / "static" / "js" / "supervisor.js").read_text(encoding="utf-8")

    assert "d.cycle" in js
    assert "d.tokens_session" in js
    assert "d.cost_session" in js
    assert "d.error_count" in js
    assert "d.queue_length" in js
    assert "renderCurrentTask(d.current_task || null)" in js
    assert "/api/system/commands" in js
    assert "/api/system/commands/execute" in js
    assert "data-supervisor-command" in js
    assert "data-supervisor-command-run" in js
    assert "insertSupervisorCommand" in js
    assert "executeSlashCommand" in js
    assert "dispatch.summary" in js
    assert "syncStreamFilter" in js
    assert "updateStreamSummary" in js
    assert "clearEntries" in js
    assert "(a.model_name || 'N/A')" in js
    assert '>Resume</button>' in js
    assert '>Pause</button>' in js
    assert '>Stop</button>' in js
    assert '▶ Resume' not in js
    assert '⏸ Pause' not in js
    assert '⏹ Stop' not in js
    assert '✓ command executed' not in js
    assert '✗ network error' not in js


def test_system_commands_endpoint_exposes_shared_operator_catalog() -> None:
    app = Starlette(routes=[Route("/api/system/commands", system_commands, methods=["GET"])])
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/system/commands")

    assert response.status_code == 200
    payload = response.json()
    assert "commands" in payload
    assert any(item["command"] == "/help" for item in payload["commands"])
    assert any(item["command"] == "/queue-status" for item in payload["commands"])
    assert any(item["web_support"] == "unsupported" for item in payload["commands"])


def test_system_command_execute_rejects_unsupported_command() -> None:
    app = Starlette(routes=[Route("/api/system/commands/execute", system_command_execute, methods=["POST"])])
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/system/commands/execute", json={"command": "/exit"})

    assert response.status_code == 409
    assert response.json()["web_support"] == "unsupported"


def test_live_stream_component_supports_source_coloring() -> None:
    js = (_WEB_ROOT / "static" / "js" / "components" / "live-stream.js").read_text(encoding="utf-8")

    assert "stream-source-executor" in js
    assert "stream-source-supervisor" in js
    assert "stream-source-system" in js
    assert "stream-source-user" in js
    assert "entry-chip--source" in js
    assert "entry-chip--target" in js
    assert "entry-chip--type" in js
    assert "to all" in js
    assert "_formatMessage(msg)" in js
    assert "_buildMetaChips(meta)" in js
    assert "setFilter(filter)" in js
    assert "clearEntries()" in js
    assert "_matchesFilter(entry)" in js
    assert "operator.input.accepted" in js
    assert "Current task" in js
