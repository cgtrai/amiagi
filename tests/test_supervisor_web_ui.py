from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.domain.task import TaskStatus
from amiagi.infrastructure.energy_cost_tracker import EnergySummary
from amiagi.interfaces.web.routes.system_routes import system_command_execute, system_commands, system_state


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


def _make_client(**state_attrs) -> TestClient:
    app = Starlette(routes=[Route("/api/system/state", system_state, methods=["GET"])])
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_system_state_includes_gpu_and_session_metrics_and_current_task() -> None:
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

    with patch(
        "amiagi.interfaces.web.routes.system_routes._gpu_summary",
        return_value={"gpu_ram_used_pct": 9, "gpu_usage_pct": 75},
    ):
        response = client.get("/api/system/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gpu_ram_used_pct"] == 9
    assert payload["tokens_session"] == 321
    assert payload["cost_session"] == 12.5
    assert payload["cost_currency"] == "USD"
    assert payload["gpu_usage_pct"] == 75
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


def test_system_state_adds_energy_cost_and_uses_tracker_currency() -> None:
    task_queue = MagicMock()
    task_queue.pending_count.return_value = 0
    task_queue.total_count.return_value = 0
    task_queue.stats.return_value = {"in_progress": 0, "pending": 0}
    task_queue.list_all.return_value = []

    budget_manager = SimpleNamespace(
        session_budget=SimpleNamespace(tokens_used=2000, spent_usd=1.5),
        currency="PLN",
    )
    tracker = MagicMock()
    tracker.summary.return_value = EnergySummary(
        total_energy_wh=42.0,
        total_cost_local=0.75,
        price_per_kwh=1.0,
        currency="PLN",
        total_requests=2,
        gpu_power_limit_w=300.0,
        avg_power_draw_w=150.0,
        total_inference_seconds=18.0,
    )
    chat_service = SimpleNamespace(energy_tracker=tracker)

    client = _make_client(
        task_queue=task_queue,
        budget_manager=budget_manager,
        chat_service=chat_service,
    )

    response = client.get("/api/system/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tokens_session"] == 2000
    assert payload["cost_session"] == 2.25
    assert payload["cost_currency"] == "PLN"


def test_supervisor_template_exposes_updated_state_cards_and_actions() -> None:
    html = (_WEB_ROOT / "templates" / "supervisor.html").read_text(encoding="utf-8")
    drawer_partial = (_WEB_ROOT / "templates" / "partials" / "detail_drawer.html").read_text(encoding="utf-8")
    base_html = (_WEB_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert 'id="sv-gpu-ram"' in html
    assert 'id="sv-tokens-count"' in html
    assert 'id="sv-session-cost"' in html
    assert 'id="sv-gpu-usage"' in html
    assert 'id="sv-cycle-count"' not in html
    assert 'id="sv-errors-count"' not in html
    assert "supervisor.gpu_ram" in html
    assert "supervisor.gpu_usage" in html
    assert 'class="glass-card supervisor-topbar"' in html
    assert 'class="supervisor-metric"' in html
    assert 'class="supervisor-metric-group supervisor-metric-group--technical"' in html
    assert 'class="supervisor-metric-group supervisor-metric-group--financial"' in html
    assert 'id="supervisor-connection-banner"' in html
    assert 'class="supervisor-workspace"' in html
    assert 'class="supervisor-primary"' in html
    assert 'class="supervisor-sidebar"' in html
    assert 'id="supervisor-console"' in html
    assert 'class="supervisor-command-panel"' in html
    assert 'id="btn-sv-add-agent"' in html
    assert 'id="spawn-agent-section"' not in html
    assert 'class="supervisor-actions"' not in html
    assert 'class="supervisor-cards"' not in html
    assert 'id="sv-queue-count"' not in html
    assert "supervisor.current_task" in html
    assert "supervisor.refresh_view" in html
    assert 'id="btn-sv-commands"' in html
    assert "supervisor.system_commands" in html
    assert 'id="btn-sv-new-prompt"' not in html
    assert 'id="btn-sv-queue"' not in html
    assert 'id="sv-stream-channel-filter"' in html
    assert 'id="sv-stream-level-filter"' in html
    assert 'id="btn-sv-stream-clear"' in html
    assert 'id="supervisor-stream-summary"' not in html
    assert 'id="sv-stream-source"' not in html
    assert 'thread-owner="supervisor"' in html
    assert "supervisor.queue" not in html
    assert "supervisor.reset_session" in html
    assert 'id="btn-sv-history"' in html
    assert "supervisor.history" in html
    assert "supervisor.live_stream" in html
    assert "supervisor.command_panel" in html
    assert html.index('class="supervisor-command-panel"') > html.index('id="supervisor-live-stream"')
    assert 'data-current-user-label="{{ current_user_label | e }}"' in html
    assert html.count('id="operator-input-text"') == 1
    assert 'agent-input-form' not in html
    assert 'agent-input-text' not in html
    assert 'data-agent-input' not in html
    assert 'detail-drawer-resize-handle' not in drawer_partial
    assert '{% block body_class %}page-supervisor page-low-gpu{% endblock %}' in html
    assert '<body class="{% block body_class %}{% endblock %}">' in base_html


def test_supervisor_js_renders_extended_state_and_agent_models() -> None:
    js = (_WEB_ROOT / "static" / "js" / "supervisor.js").read_text(encoding="utf-8")

    assert "d.gpu_ram_used_pct" in js
    assert "d.tokens_session" in js
    assert "d.cost_session" in js
    assert "formatSessionCost" in js
    assert "formatPercent" in js
    assert "d.cost_currency" in js
    assert "d.gpu_usage_pct" in js
    assert "d.queue_length" in js
    assert "sv-gpu-ram" in js
    assert "sv-gpu-usage" in js
    assert "d.cycle" not in js
    assert "d.error_count" not in js
    assert "renderCurrentTask(d.current_task || null)" in js
    assert "/api/system/commands" in js
    assert "/api/system/commands/execute" in js
    assert "data-supervisor-command" in js
    assert "insertSupervisorCommand" in js
    assert "window.supervisorInsertCommand = insertSupervisorCommand" in js
    assert "getCurrentUserLabel()" in js
    assert "dataset.currentUserLabel" in js
    assert "executeSlashCommand" in js
    assert "d.dispatch" not in js
    assert "syncStreamFilter" in js
    assert "updateStreamSummary" not in js
    assert "clearEntries" in js
    assert "agent-model-link" in js
    assert "data-agent-setup-url" in js
    assert "window.location.href = setupUrl" in js
    assert "role=\"link\"" in js
    assert "assign_model_required" in js
    assert "INPUT_HISTORY_LIMIT = 50" in js
    assert "sessionStorage.getItem(INPUT_HISTORY_KEY)" in js
    assert "ArrowUp" in js
    assert "ArrowDown" in js
    assert "detail-drawer--wide" in js
    assert "data-supervisor-drawer-toggle" in js
    assert "Command copied to input" in js
    assert "closeDetailDrawer()" in js
    assert "!== 'unsupported'" in js
    assert 'onclick="window.supervisorInsertCommand && window.supervisorInsertCommand(this.dataset.supervisorCommand)"' in js
    assert "item.web_note" not in js
    assert "appendSupervisorStreamMessage" in js
    assert "appendLogStyleStreamEntry" in js
    assert "normalizeCommandOutputLines" in js
    assert "operator.command.output" in js
    assert "appendLogStyleStreamEntry(getCurrentUserLabel(), line" in js
    assert "liveStream.append(dispatch.summary" not in js
    assert "appendLogStyleStreamEntry(getCurrentUserLabel(), message" not in js
    assert "Command Output" not in js
    assert "Command executed: " not in js
    assert "REFRESH_INTERVAL_MS = 30000" in js
    assert "document.visibilityState !== 'visible'" in js
    assert "lastAgentsSnapshot" in js
    assert "lastTargetSnapshot" in js
    assert "stream-connection" in js
    assert "setStreamConnectionState" in js
    assert "Connection lost. Reconnecting in " in js
    assert "renderSpawnAgentDrawer" in js
    assert "submitSpawnAgent" in js
    assert "AGENT_THREAD_ENTRY_LIMIT = 80" in js
    assert "actorStateSnapshot = new Map()" in js
    assert "agent_id: 'router'" in js
    assert "name: 'Router'" in js
    assert "role: 'system'" in js
    assert "actorStateSnapshot.get('router')" in js
    assert "data-agent-toggle" not in js
    assert "toggleAgentPanel" not in js
    assert "agent-control-row--collapsed" not in js
    assert "isEditableTarget" in js
    assert "focusOperatorInput" in js
    assert "focusAgentSummaryByOffset" in js
    assert "e.key === '/'" in js
    assert "e.key === 'Escape'" in js
    assert "e.key === 'j'" in js
    assert "e.key === 'k'" in js
    assert "agentLastActivity = new Map()" in js
    assert "formatLastActivityLabel" in js
    assert "updateAgentLastActivity" in js
    assert "agent-last-activity" in js
    assert "agentThreadDroppedCounts = new Map()" in js
    assert "agentThreadScrollFrames = new Map()" in js
    assert "agentThreadAutoScroll = new Map()" in js
    assert "pendingActions = new Set()" in js
    assert "runSingleFlight(key, targets, callback)" in js
    assert "setPendingUiState(targets, true)" in js
    assert "Retention active: showing last " in js
    assert "agent-thread-retention" in js
    assert "detail.from || detail.source_label" in js
    assert "agent-thread-entry__chips" in js
    assert "entry-chip entry-chip--source" in js
    assert "entry-chip entry-chip--target" in js
    assert "entry-chip entry-chip--type" in js
    assert "entry-chip entry-chip--status" in js
    assert "stream-source-supervisor" in js
    assert "streamSessionId = ''" in js
    assert "streamActiveAgents = []" in js
    assert "withSupervisorAgents" in js
    assert "is_virtual: true" in js
    assert "routeAgentThreadEvent" in js
    assert "scheduleAgentThreadScrollToBottom" in js
    assert "AGENT_THREAD_AUTO_SCROLL_THRESHOLD = 60" in js
    assert "stateLabel(state)" in js
    assert "stateIconMarkup(state)" in js
    assert "supervisor.state_idle" in js
    assert "supervisor.state_working" in js
    assert "supervisor.state_paused" in js
    assert "supervisor.state_error" in js
    assert "supervisor.state_terminated" in js
    assert "screen.dataset.autoscrollBound = 'true'" in js
    assert "screen.addEventListener('scroll'" in js
    assert "distanceFromBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight" in js
    assert "agentThreadAutoScroll.set(normalizedId, distanceFromBottom < AGENT_THREAD_AUTO_SCROLL_THRESHOLD)" in js
    assert "if (!shouldAutoScrollAgentThread(normalizedId)) return;" in js
    assert "screen.scrollTop = screen.scrollHeight" in js
    assert "actorStateSnapshot.set(String(detail.actor).toLowerCase()" in js
    assert "thread_owners" in js
    assert "agent-thread-screen__list" in js
    assert "supervisor.agent_waiting" in js
    assert "btn-sv-add-agent" in js
    assert "spawn-agent-drawer-form" in js
    assert "runSingleFlight('operator-input'" in js
    assert "runSingleFlight('spawn-agent'" in js
    assert "runSingleFlight('session-reset'" in js
    assert "runSingleFlight('agent:' + id + ':' + action" in js
    assert "bindCurrentTaskAction('ct-btn-pause'" in js
    assert "supervisor.last_activity" in js
    assert "workflows.resume" in js
    assert "supervisor.pause_agent" in js
    assert "supervisor.stop_agent" in js
    assert "btn-sv-new-prompt" not in js
    assert "iconMarkup('resume')" not in js
    assert '▶ Resume' not in js
    assert '⏸ Pause' not in js
    assert '⏹ Stop' not in js
    assert 'Communication' not in js
    assert '✓ command executed' not in js
    assert '✗ network error' not in js
    live_stream_js = (_WEB_ROOT / "static" / "js" / "components" / "live-stream.js").read_text(encoding="utf-8")
    assert "_lastRenderedSignature = null" in live_stream_js
    assert "_buildDedupSignature(text, level, channel, meta)" in live_stream_js
    assert "_isConsecutiveDuplicate(text, level, channel, meta, timestampText)" in live_stream_js
    assert "entryTimestampMs - this._lastRenderedAtMs <= 5000" in live_stream_js
    assert "if (this._isConsecutiveDuplicate(text, level, channel, meta, timestampText))" in live_stream_js


def test_supervisor_css_supports_expandable_drawer_and_model_warning() -> None:
    css = (_WEB_ROOT / "static" / "css" / "supervisor.css").read_text(encoding="utf-8")

    assert ".detail-drawer.detail-drawer--wide" in css
    assert "width: min(calc(var(--drawer-width) + 30vw), 80vw);" in css
    assert "z-index: calc(var(--z-modal) + 2);" in css
    assert ".agent-model-link--warning" in css
    assert ".agent-control-row--warning" in css
    assert ".agent-control-row--link" in css
    assert ".agent-control-row__summary:focus-visible" in css
    assert "max-width: min(100%, 450px);" in css
    assert ".agent-last-activity" in css
    assert ".agent-state--terminated" in css
    assert ".agent-state svg" in css
    assert ".agent-control-action" in css
    assert ".agent-control-action--danger" in css
    assert "flex-wrap: nowrap;" in css
    assert "flex: 0 0 max-content;" in css
    assert "min-width: max-content;" in css
    assert "white-space: nowrap;" in css
    assert ".agent-thread-retention" in css
    assert '.glass-btn[aria-busy="true"]' in css
    assert ".operator-input-field[aria-busy=\"true\"]" in css
    assert ".agent-thread-screen" in css
    assert "--stream-row-height: 1.35rem;" in css
    assert "--supervisor-screen-rows: 18;" in css
    assert "--agent-screen-rows: 10;" in css
    assert "--supervisor-screen-height: calc(var(--stream-row-height) * var(--supervisor-screen-rows));" in css
    assert "--agent-screen-height: calc(var(--stream-row-height) * var(--agent-screen-rows));" in css
    assert "--supervisor-read-width: 120ch;" in css
    assert "min-height: var(--agent-screen-height, calc(var(--stream-row-height, 1.35rem) * 10));" in css
    assert "height: var(--agent-screen-height, calc(var(--stream-row-height, 1.35rem) * 10));" in css
    assert "overflow-y: auto;" in css
    assert ".agent-thread-screen__list" in css
    assert ".agent-thread-entry" in css
    assert ".agent-thread-entry__chips" in css
    assert ".entry-chip--source" in css
    assert ".entry-chip--target" in css
    assert ".entry-chip--type" in css
    assert ".entry-chip--status" in css
    assert ".stream-source-supervisor" in css
    assert ".supervisor-command-item__content" in css
    assert "body.page-supervisor" in css
    assert "backdrop-filter: none !important;" in css
    assert "animation: none !important;" in css
    assert "contain: layout style paint;" in css
    assert ".supervisor-topbar" in css
    assert ".supervisor-metric" in css
    assert ".supervisor-connection-banner" in css
    assert ".supervisor-workspace" in css
    assert "width: 100%;" in css
    assert "max-width: 100%;" in css
    assert "grid-template-columns: minmax(0, min(var(--supervisor-read-width, 120ch), 58vw)) minmax(320px, 1fr);" in css
    assert "justify-content: start;" in css
    assert "width: 100%;" in css
    assert "margin-inline: 0;" in css
    assert ".supervisor-sidebar" in css
    assert "position: sticky;" in css
    assert ".supervisor-command-panel" in css
    assert "#agent-control" in css
    assert "overflow: hidden;" in css
    assert ".agent-control-list" in css
    assert "flex: 1 1 auto;" in css
    assert ".agent-control-row" in css
    assert "flex: 0 0 auto;" in css
    assert "#supervisor-live-stream" in css
    assert "min-height: var(--supervisor-screen-height, calc(var(--stream-row-height, 1.35rem) * 18));" in css


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

    assert "_currentUserLabel()" in js
    assert "this.closest('.supervisor-main')" in js
    assert "root.dataset.currentUserLabel" in js
    assert "_resolveDisplayLabel(label)" in js
    assert "normalized === 'Sponsor' || normalized === 'Operator'" in js
    assert "stream-source-executor" in js
    assert "stream-source-supervisor" in js
    assert "stream-source-system" in js
    assert "stream-source-user" in js
    assert "entry-chip--source" in js
    assert "entry-chip--target" in js
    assert "entry-chip--type" in js
    assert "entry-chip--status" in js
    assert "to all" in js
    assert "_formatMessage(msg)" in js
    assert "_buildMetaChips(meta)" in js
    assert "const sourceLabel = this._resolveDisplayLabel((msg && (msg.from || msg.source_label)) || 'Operator');" in js
    assert "const targetLabel = this._resolveDisplayLabel(targetAgent);" in js
    assert '`${sourceLabel} → ${targetLabel}: ${message}`' in js
    assert '`${sourceLabel} → all: ${message}`' in js
    assert "msg.message_type || msg.type" in js
    assert "meta.from || meta.source_label" in js
    assert "meta.to || meta.target_agent" in js
    assert "setFilter(filter)" in js
    assert "clearEntries()" in js
    assert "_matchesFilter(entry)" in js
    assert "_matchesThreadOwner(msg)" in js
    assert "thread-owner" in js
    assert "operator.input.accepted" in js
    assert "Current task" in js
    assert "min-height: var(--supervisor-screen-height, calc(var(--stream-row-height, 1.35rem) * 18));" in js
    assert "height: var(--supervisor-screen-height, calc(var(--stream-row-height, 1.35rem) * 18));" in js
    assert "overflow: hidden;" in js
    assert "overflow-y: auto;" in js
    assert "max-height: 400px;" not in js
    assert 'msg.type === "ping"' in js
    assert 'type: "pong"' in js
    assert 'msg.type === "stream.config"' in js
    assert 'msg.type === "stream.history"' in js
    assert 'this.dispatchEvent(new CustomEvent("stream-event", {' in js
    assert 'detail: Object.assign({ replayed: true }, event)' in js
    assert 'this._matchesThreadOwner(event)' in js
    assert 'this._rememberServerEvent(event)' in js
    assert '_lastEventId = 0' in js
    assert '_seenServerEventIds = new Set()' in js
    assert 'url.searchParams.set("since_id", String(this._lastEventId))' in js
    assert 'Reconnect gap exceeded retention; replaying the latest available events' in js
    assert 'stream-connection' in js
    assert 'Math.pow(2, this._reconnectAttempts)' in js
    assert '_maxReconnectDelay = 30000' in js
    assert "_appendRenderedEntry(entry)" in js
    assert "_removeRenderedEntry(removed)" in js
    assert "if (!owners.length) {" in js
    assert "return false;" in js

