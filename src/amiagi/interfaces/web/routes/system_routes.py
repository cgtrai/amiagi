"""System-level and extended operator API routes.

Endpoints:
    GET   /api/system/state        — consolidated system state (2.3)
    POST  /api/system/input        — operator text input / command (2.4)
    POST  /api/inbox/{id}/delegate — delegate inbox item to agent (2.9)
    POST  /api/agents/spawn        — spawn a new agent instance (2.11)
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import logging
import time
from contextlib import redirect_stdout
from pathlib import Path

from amiagi.domain.task import TaskStatus
from amiagi.interfaces.cli import HELP_TEXT
from amiagi.interfaces.cli_commands import CliContext, collect_capabilities, dispatch_cli_command
from amiagi.interfaces.shared_cli_helpers import _build_operator_command_catalog, _web_command_support
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


def _list_tasks(task_queue) -> list:
    if task_queue is None:
        return []
    if hasattr(task_queue, "list_all"):
        return task_queue.list_all()
    if hasattr(task_queue, "_tasks") and isinstance(task_queue._tasks, dict):
        return list(task_queue._tasks.values())
    return []


def _find_running_task(task_queue):
    tasks = _list_tasks(task_queue)
    running = [
        t for t in tasks
        if str(getattr(getattr(t, "status", ""), "value", getattr(t, "status", ""))).lower() in ("in_progress", "running")
    ]
    return running[0] if running else None


def _current_task_payload(task, model_name: str | None = None) -> dict | None:
    if not task:
        return None
    return {
        "task_id": task.task_id,
        "title": getattr(task, "title", ""),
        "agent_id": getattr(task, "assigned_agent_id", None),
        "model_name": model_name,
        "progress_pct": getattr(task, "progress_pct", 0),
        "steps_done": getattr(task, "steps_done", 0),
        "steps_total": getattr(task, "steps_total", 0),
    }


def _operator_dispatch_payload(message: str, target_agent: str | None, submitted_message: str) -> dict:
    normalized_target = str(target_agent or "").strip() or None
    return {
        "channel": "user",
        "actor": "operator",
        "target_agent": normalized_target,
        "target_scope": "agent" if normalized_target else "broadcast",
        "message": message,
        "submitted_message": submitted_message,
        "summary": (
            f"Operator → {normalized_target}: {message}"
            if normalized_target
            else f"Operator → all: {message}"
        ),
    }


async def _broadcast_if_available(request: Request, event_type: str, payload: dict) -> None:
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        await hub.broadcast(event_type, payload)


# ── GET /api/system/state ────────────────────────────────────

async def system_state(request: Request) -> JSONResponse:
    """Consolidated system state — single endpoint for supervisor UI.

    Returns agent count, task count, inbox pending, uptime, and
    active model name in one payload instead of 4 separate fetches.
    """
    state = request.app.state

    result: dict = {}

    # Agents
    registry = getattr(state, "agent_registry", None)
    if registry is not None:
        try:
            agents = registry.list_all()
            result["agents"] = {
                "total": len(agents),
                "by_state": {},
            }
            for a in agents:
                s = str(getattr(a.state, "value", a.state))
                result["agents"]["by_state"][s] = result["agents"]["by_state"].get(s, 0) + 1
        except Exception:
            result["agents"] = {"total": 0, "by_state": {}}
    else:
        result["agents"] = {"total": 0, "by_state": {}}

    # Tasks
    task_queue = getattr(state, "task_queue", None)
    current_task = _find_running_task(task_queue)
    if task_queue is not None:
        try:
            task_stats = task_queue.stats() if hasattr(task_queue, "stats") else {}
            result["tasks"] = {
                "pending": task_queue.pending_count(),
                "total": task_queue.total_count() if hasattr(task_queue, "total_count") else task_queue.pending_count(),
                "running": int(task_stats.get("in_progress", 0)) + int(task_stats.get("running", 0)) if isinstance(task_stats, dict) else 0,
            }
        except Exception:
            result["tasks"] = {"pending": 0, "total": 0, "running": 0}
    else:
        result["tasks"] = {"pending": 0, "total": 0, "running": 0}

    # Inbox
    inbox_svc = getattr(state, "inbox_service", None)
    if inbox_svc is not None:
        try:
            counts = await inbox_svc.count_by_status()
            result["inbox"] = {
                "pending": counts.get("pending", 0),
                "approved": counts.get("approved", 0),
                "rejected": counts.get("rejected", 0),
                "total": sum(counts.values()),
            }
        except Exception:
            result["inbox"] = {"pending": 0, "total": 0}
    else:
        result["inbox"] = {"pending": 0, "total": 0}

    # Uptime
    startup_ts = getattr(state, "_startup_time", None)
    if startup_ts is not None:
        elapsed = int(time.time() - startup_ts)
        result["uptime_seconds"] = elapsed
        if elapsed < 60:
            result["uptime"] = f"{elapsed}s"
        elif elapsed < 3600:
            result["uptime"] = f"{elapsed // 60}m"
        else:
            h, m = divmod(elapsed // 60, 60)
            result["uptime"] = f"{h}h {m}m"
    else:
        result["uptime_seconds"] = 0
        result["uptime"] = "0s"

    # Model
    try:
        import json as _json
        from pathlib import Path
        model_cfg = Path("data/model_config.json")
        if model_cfg.exists():
            with open(model_cfg) as f:
                mcfg = _json.load(f)
            result["model"] = mcfg.get("polluks_model") or mcfg.get("kastor_model") or "—"
        else:
            result["model"] = "—"
    except Exception:
        result["model"] = "—"

    # Workflow engine
    wf_engine = getattr(state, "workflow_engine", None)
    if wf_engine is not None:
        try:
            runs = getattr(wf_engine, "_runs", {})
            result["workflows"] = {
                "active_runs": len(runs),
            }
        except Exception:
            result["workflows"] = {"active_runs": 0}
    else:
        result["workflows"] = {"active_runs": 0}

    # Extended fields (S4)
    budget_manager = getattr(state, "budget_manager", None)
    result["cycle"] = getattr(state, "cycle_count", 0)
    result["tokens_session"] = budget_manager.session_budget.tokens_used if budget_manager and hasattr(budget_manager, "session_budget") else 0
    result["cost_session"] = float(budget_manager.session_budget.spent_usd) if budget_manager and hasattr(budget_manager, "session_budget") else 0.0
    result["error_count"] = getattr(state, "error_count", 0)
    result["queue_length"] = task_queue.pending_count() if task_queue and hasattr(task_queue, "pending_count") else 0

    agent_id = getattr(current_task, "assigned_agent_id", None) if current_task else None
    model_name = None
    if agent_id:
        registry = getattr(state, "agent_registry", None)
        if registry:
            try:
                agent = registry.get(agent_id)
                if agent:
                    model_name = getattr(agent, "model_name", None)
            except Exception:
                model_name = None
    result["current_task"] = _current_task_payload(current_task, model_name)

    return JSONResponse(result)


# ── GET /api/system/current-task (S3, S8) ────────────────────

async def system_current_task(request: Request) -> JSONResponse:
    """Return the currently running task, if any."""
    state = request.app.state
    task_queue = getattr(state, "task_queue", None)
    if task_queue is None:
        return JSONResponse(None)

    task = _find_running_task(task_queue)
    if not task:
        return JSONResponse(None)

    agent_id = getattr(task, "assigned_agent_id", None)
    model_name = None
    if agent_id:
        registry = getattr(state, "agent_registry", None)
        if registry:
            agent = registry.get(agent_id)
            if agent:
                model_name = getattr(agent, "model_name", None)

    return JSONResponse(_current_task_payload(task, model_name))


# ── GET /api/system/commands ────────────────────────────────

async def system_commands(request: Request) -> JSONResponse:
    """Return the shared operator command catalog for Supervisor WWW."""
    return JSONResponse({"commands": _build_operator_command_catalog()})


def _build_web_cli_context(request: Request) -> CliContext:
    adapter = getattr(request.app.state, "web_adapter", None)
    router_engine = getattr(adapter, "router_engine", None)
    if router_engine is None:
        raise RuntimeError("router_engine unavailable")

    shell_policy = getattr(request.app.state, "shell_policy", None)
    if shell_policy is None:
        shell_policy_path = getattr(router_engine, "shell_policy_path", None)
        try:
            shell_policy = load_shell_policy(shell_policy_path) if shell_policy_path else default_shell_policy()
        except Exception:
            shell_policy = default_shell_policy()

    def _log_action(*_args, **_kwargs) -> None:
        return None

    return CliContext(
        chat_service=router_engine.chat_service,
        permission_manager=router_engine.permission_manager,
        script_executor=router_engine.script_executor,
        work_dir=Path(router_engine.work_dir),
        workspace_root=Path.cwd(),
        shell_policy=shell_policy,
        autonomous_mode=bool(getattr(router_engine, "autonomous_mode", False)),
        log_action=_log_action,
        collect_capabilities=lambda **kwargs: collect_capabilities(
            permission_manager=router_engine.permission_manager,
            autonomous_mode=bool(getattr(router_engine, "autonomous_mode", False)),
            check_network=bool(kwargs.get("check_network", False)),
        ),
    )


async def system_command_execute(request: Request) -> JSONResponse:
    """Execute supported CLI/TUI slash commands from Supervisor WWW."""
    body = await request.json()
    command = str(body.get("command") or "").strip()
    if not command:
        return JSONResponse({"error": "command required"}, status_code=400)
    if not command.startswith("/"):
        return JSONResponse({"error": "slash command required"}, status_code=400)

    support = _web_command_support(command)
    if support != "run":
        return JSONResponse({
            "error": "command not supported in WWW before UAT",
            "command": command,
            "web_support": support,
        }, status_code=409)

    adapter = getattr(request.app.state, "web_adapter", None)
    router_engine = getattr(adapter, "router_engine", None)
    if router_engine is None:
        return JSONResponse({"error": "router_engine unavailable"}, status_code=503)

    ctx = _build_web_cli_context(request)
    stdout = StringIO()
    try:
        with redirect_stdout(stdout):
            result = dispatch_cli_command(command, ctx, router_engine=router_engine, help_text=HELP_TEXT)
    except Exception as exc:
        logger.exception("system command execute failed")
        return JSONResponse({"error": str(exc), "command": command}, status_code=500)

    output = stdout.getvalue().strip()
    if not result.handled:
        return JSONResponse({"error": "unsupported command", "command": command}, status_code=400)

    await _broadcast_if_available(request, "operator.command.executed", {
        "channel": "user",
        "actor": "operator",
        "source_label": "Operator",
        "command": command,
        "summary": f"Operator command executed: {command}",
    })

    return JSONResponse({
        "ok": True,
        "command": command,
        "output": output,
        "should_exit": bool(result.should_exit),
        "web_support": support,
    })


# ── POST /api/system/current-task/{action} ────────────────────

async def system_current_task_pause(request: Request) -> JSONResponse:
    """Pause the currently running task if the queue supports it.

    Fallback behavior: move the running task back to ASSIGNED state.
    """
    task_queue = getattr(request.app.state, "task_queue", None)
    task = _find_running_task(task_queue)
    if task is None:
        return JSONResponse({"error": "no current task"}, status_code=404)

    task.status = TaskStatus.ASSIGNED
    await _broadcast_if_available(request, "system.current_task.paused", {"task_id": task.task_id})
    return JSONResponse({"ok": True, "task_id": task.task_id, "status": task.status.value})


async def system_current_task_stop(request: Request) -> JSONResponse:
    """Stop the currently running task by cancelling it."""
    task_queue = getattr(request.app.state, "task_queue", None)
    task = _find_running_task(task_queue)
    if task is None:
        return JSONResponse({"error": "no current task"}, status_code=404)

    if hasattr(task, "cancel"):
        task.cancel()
    else:
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc)

    await _broadcast_if_available(request, "system.current_task.stopped", {"task_id": task.task_id})
    return JSONResponse({"ok": True, "task_id": task.task_id, "status": task.status.value})


async def system_current_task_retry(request: Request) -> JSONResponse:
    """Retry the currently selected task by moving it back to pending."""
    task_queue = getattr(request.app.state, "task_queue", None)
    task = _find_running_task(task_queue)
    if task is None:
        tasks = _list_tasks(task_queue)
        retryable = [
            t for t in tasks
            if str(getattr(getattr(t, "status", ""), "value", getattr(t, "status", ""))).lower() in ("failed", "cancelled")
        ]
        task = retryable[0] if retryable else None
    if task is None:
        return JSONResponse({"error": "no task available for retry"}, status_code=404)

    task.status = TaskStatus.PENDING
    task.assigned_agent_id = None
    task.started_at = None
    task.completed_at = None
    task.result = ""

    await _broadcast_if_available(request, "system.current_task.retried", {"task_id": task.task_id})
    return JSONResponse({"ok": True, "task_id": task.task_id, "status": task.status.value})


# ── POST /api/system/reset (S7) ─────────────────────────────

async def system_reset(request: Request) -> JSONResponse:
    """Reset session — clear budget counters and optionally router state."""
    state = request.app.state

    budget = getattr(state, "budget_manager", None)
    if budget and hasattr(budget, "reset_all"):
        budget.reset_all()

    # Reset cycle counter
    if hasattr(state, "cycle_count"):
        state.cycle_count = 0
    if hasattr(state, "error_count"):
        state.error_count = 0

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "system.reset", {})
    return JSONResponse({"ok": True, "message": "Session reset"})


# ── POST /api/system/input ───────────────────────────────────

async def system_input(request: Request) -> JSONResponse:
    """Inject operator text command into the RouterEngine.

    Body: { "message": "...", "target_agent": "optional-id" }
    """
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    target_agent = body.get("target_agent")

    adapter = getattr(request.app.state, "web_adapter", None)
    if adapter is None:
        return JSONResponse({"error": "web_adapter unavailable"}, status_code=503)

    try:
        submitted_message = message
        if target_agent and not message.startswith("["):
            submitted_message = f"[Sponsor -> {target_agent}] {message}"

        dispatch_payload = _operator_dispatch_payload(message, target_agent, submitted_message)

        adapter.submit_user_turn(submitted_message)
        await _broadcast_if_available(request, "operator.input.accepted", dispatch_payload)

        # Log the operator action
        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "system.input", {
            "message": message[:200],
            "target_agent": target_agent,
        })

        return JSONResponse({
            "ok": True,
            "response": "accepted",
            "submitted_message": submitted_message,
            "dispatch": dispatch_payload,
        })

    except Exception as exc:
        logger.exception("system.input failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── POST /api/inbox/{item_id}/delegate ───────────────────────

async def inbox_delegate(request: Request) -> JSONResponse:
    """Delegate an inbox item to a specific agent.

    Body: { "agent_id": "target-agent", "instructions": "optional note" }
    """
    inbox_svc = getattr(request.app.state, "inbox_service", None)
    if inbox_svc is None:
        return JSONResponse({"error": "inbox_service unavailable"}, status_code=503)

    item_id = request.path_params["item_id"]
    body = await request.json()
    agent_id = (body.get("agent_id") or body.get("delegate_to") or "").strip()
    instructions = body.get("instructions", "")

    if not agent_id:
        return JSONResponse({"error": "agent_id required"}, status_code=400)

    # Verify item exists
    item = await inbox_svc.get(item_id)
    if item is None:
        return JSONResponse({"error": "item not found"}, status_code=404)

    if item.status != "pending":
        return JSONResponse({"error": "item already resolved"}, status_code=409)

    # Resolve as delegated
    resolved = await inbox_svc._resolve(
        item_id,
        resolution="delegated",
        resolved_by="operator",
        reason=f"Delegated to {agent_id}: {instructions}" if instructions else f"Delegated to {agent_id}",
    )

    # Create a new inbox item targeted at the agent
    delegated_item = await inbox_svc.create(
        item_type=item.item_type,
        title=f"[Delegated] {item.title}",
        body=f"{item.body}\n\n--- Operator instructions ---\n{instructions}" if instructions else item.body,
        source_type="delegation",
        source_id=item_id,
        agent_id=agent_id,
        priority=item.priority,
        metadata={"original_item_id": item_id, "delegated_by": "operator"},
    )

    # Broadcast
    await _broadcast_if_available(request, "inbox.delegated", {
        "original_item_id": item_id,
        "new_item_id": delegated_item.id,
        "agent_id": agent_id,
    })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.delegate", {
        "item_id": item_id,
        "agent_id": agent_id,
    })

    return JSONResponse({
        "ok": True,
        "original_item": resolved.to_dict() if resolved else None,
        "delegated_item": delegated_item.to_dict(),
    })


# ── POST /api/agents/spawn ──────────────────────────────────

async def agent_spawn(request: Request) -> JSONResponse:
    """Spawn a new agent instance from the UI.

    Body: { "name": "agent-name", "role": "executor", "model": "optional" }
    """
    body = await request.json()
    name = (body.get("name") or "").strip()
    role = body.get("role", "executor")
    model = body.get("model")

    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    factory = getattr(request.app.state, "agent_factory", None)
    registry = getattr(request.app.state, "agent_registry", None)

    if factory is None:
        return JSONResponse({"error": "agent_factory unavailable"}, status_code=503)

    try:
        kwargs: dict = {"name": name, "role": role}
        if model:
            kwargs["model"] = model

        # AgentFactory.create() returns an AgentDescriptor or similar
        agent = factory.create(**kwargs)

        # Register if registry available
        if registry is not None and hasattr(registry, "register"):
            registry.register(agent)

        agent_id = getattr(agent, "agent_id", None) or getattr(agent, "id", str(agent))

        # Broadcast
        await _broadcast_if_available(request, "agent.spawned", {
            "agent_id": str(agent_id),
            "name": name,
            "role": role,
        })

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "agent.spawn", {
            "agent_id": str(agent_id),
            "name": name,
            "role": role,
        })

        return JSONResponse({
            "ok": True,
            "agent_id": str(agent_id),
            "name": name,
            "role": role,
        }, status_code=201)

    except Exception as exc:
        logger.exception("agent.spawn failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Route table ──────────────────────────────────────────────

system_routes: list[Route] = [
    Route("/api/system/state", system_state, methods=["GET"]),
    Route("/api/system/current-task", system_current_task, methods=["GET"]),
    Route("/api/system/commands", system_commands, methods=["GET"]),
    Route("/api/system/commands/execute", system_command_execute, methods=["POST"]),
    Route("/api/system/current-task/pause", system_current_task_pause, methods=["POST"]),
    Route("/api/system/current-task/stop", system_current_task_stop, methods=["POST"]),
    Route("/api/system/current-task/retry", system_current_task_retry, methods=["POST"]),
    Route("/api/system/reset", system_reset, methods=["POST"]),
    Route("/api/system/input", system_input, methods=["POST"]),
    Route("/api/inbox/{item_id}/delegate", inbox_delegate, methods=["POST"]),
    Route("/api/agents/spawn", agent_spawn, methods=["POST"]),
]
