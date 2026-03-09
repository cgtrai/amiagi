"""REST API routes for agents, tasks, metrics, and budget.

Provides JSON endpoints consumed by the dashboard and web components.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time as _time_module
import uuid
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.application.agent_wizard import AgentWizardService, SensitivePermissionError
from amiagi.domain.blueprint import AgentBlueprint

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Ollama connectivity cache (shared by status-bar & health)
# ------------------------------------------------------------------
_ollama_cache: dict[str, Any] = {"alive": False, "models": 0, "ts": 0.0}
_OLLAMA_CACHE_TTL = 30.0  # seconds


async def _check_ollama_cached() -> tuple[bool, int]:
    """Return (alive, model_count) with 30-s cache."""
    now = _time_module.time()
    if now - _ollama_cache["ts"] < _OLLAMA_CACHE_TTL:
        return _ollama_cache["alive"], _ollama_cache["models"]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            alive = r.status_code == 200
            models = len(r.json().get("models", [])) if alive else 0
    except Exception:
        alive, models = False, 0
    _ollama_cache.update(alive=alive, models=models, ts=now)
    return alive, models


# ------------------------------------------------------------------
# Model config cache
# ------------------------------------------------------------------
_model_cfg_cache: dict[str, Any] = {"name": "—", "ts": 0.0}
_MODEL_CFG_TTL = 60.0  # seconds


def _get_model_name_cached() -> str:
    """Return model name from data/model_config.json with 60-s cache."""
    now = _time_module.time()
    if now - _model_cfg_cache["ts"] < _MODEL_CFG_TTL:
        return _model_cfg_cache["name"]
    try:
        from pathlib import Path as _P
        import json as _json
        mcfg_path = _P("data/model_config.json")
        if mcfg_path.exists():
            with open(mcfg_path) as f:
                mcfg = _json.load(f)
            name = mcfg.get("polluks_model") or mcfg.get("kastor_model") or "—"
        else:
            name = "—"
    except Exception:
        name = "—"
    _model_cfg_cache.update(name=name, ts=now)
    return name


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _agent_to_dict(descriptor: Any) -> dict[str, Any]:
    """Serialise an ``AgentDescriptor`` to a JSON-safe dict."""
    return {
        "agent_id": descriptor.agent_id,
        "name": descriptor.name,
        "role": str(descriptor.role.value) if hasattr(descriptor.role, "value") else str(descriptor.role),
        "state": str(descriptor.state.value) if hasattr(descriptor.state, "value") else str(descriptor.state),
        "model_backend": descriptor.model_backend,
        "model_name": descriptor.model_name,
        "skills": list(descriptor.skills),
        "tools": list(descriptor.tools),
        "created_at": descriptor.created_at.isoformat() if descriptor.created_at else None,
        "persona_prompt": descriptor.persona_prompt[:200] if descriptor.persona_prompt else "",
        "metadata": descriptor.metadata or {},
    }


def _task_to_dict(task: Any) -> dict[str, Any]:
    """Serialise a ``Task`` to a JSON-safe dict."""
    metadata = getattr(task, "metadata", None) or {}
    origin = getattr(task, "origin", None) or metadata.get("origin", "system")
    return {
        "task_id": task.task_id,
        "title": task.title,
        "description": task.description,
        "priority": str(task.priority.value) if hasattr(task.priority, "value") else str(task.priority),
        "status": str(task.status.value) if hasattr(task.status, "value") else str(task.status),
        "assigned_agent_id": task.assigned_agent_id,
        "parent_task_id": task.parent_task_id,
        "dependencies": list(getattr(task, "dependencies", []) or []),
        "deadline": task.deadline.isoformat() if getattr(task, "deadline", None) else None,
        "result": getattr(task, "result", ""),
        "origin": origin,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "metadata": metadata,
    }


def _list_task_objects(task_queue: Any) -> list[Any]:
    if task_queue is None:
        return []
    if hasattr(task_queue, "list_all"):
        return task_queue.list_all()
    if hasattr(task_queue, "all_tasks"):
        return task_queue.all_tasks()
    if hasattr(task_queue, "_tasks"):
        return list(task_queue._tasks.values()) if isinstance(task_queue._tasks, dict) else list(task_queue._tasks)
    return []


def _get_task_object(task_queue: Any, task_id: str) -> Any | None:
    if task_queue is None:
        return None
    if hasattr(task_queue, "get"):
        return task_queue.get(task_id)
    if hasattr(task_queue, "_tasks") and isinstance(task_queue._tasks, dict):
        return task_queue._tasks.get(task_id)
    for task in _list_task_objects(task_queue):
        if getattr(task, "task_id", None) == task_id:
            return task
    return None


def _get_status_bar_task_counts(task_queue: Any) -> dict[str, int]:
    if task_queue is None:
        return {"running_tasks": 0, "pending_tasks": 0, "active_tasks": 0}
    running = 0
    pending = 0
    try:
        stats = task_queue.stats() if hasattr(task_queue, "stats") else {}
        if isinstance(stats, dict) and stats:
            running = int(stats.get("in_progress", 0)) + int(stats.get("running", 0))
            pending = int(stats.get("pending", 0)) + int(stats.get("assigned", 0))
        else:
            for task in _list_task_objects(task_queue):
                status = str(getattr(getattr(task, "status", ""), "value", getattr(task, "status", ""))).lower()
                if status in {"in_progress", "running"}:
                    running += 1
                elif status in {"pending", "assigned"}:
                    pending += 1
    except Exception:
        running = 0
        pending = 0
    return {
        "running_tasks": running,
        "pending_tasks": pending,
        "active_tasks": pending,
    }


async def _broadcast_web_event(request: Request, event_type: str, payload: dict[str, Any]) -> None:
    hub = getattr(request.app.state, "event_hub", None)
    if hub is None:
        return
    try:
        await hub.broadcast(event_type, payload)
    except Exception:
        logger.debug("Failed to broadcast %s", event_type, exc_info=True)


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _avg_completion_seconds(tasks: list[Any]) -> float | None:
    durations: list[float] = []
    for task in tasks:
        completed_at = getattr(task, "completed_at", None)
        started_at = getattr(task, "started_at", None) or getattr(task, "created_at", None)
        if completed_at is None or started_at is None:
            continue
        try:
            durations.append((completed_at - started_at).total_seconds())
        except Exception:
            continue
    if not durations:
        return None
    return round(sum(durations) / len(durations), 2)


def _format_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


def _workflow_run_to_dict(run: Any) -> dict[str, Any]:
    nodes = []
    workflow = getattr(run, "workflow", None)
    for node in getattr(workflow, "nodes", []) or []:
        nodes.append({
            "node_id": getattr(node, "node_id", ""),
            "node_type": getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", "")),
            "label": getattr(node, "label", ""),
            "description": getattr(node, "description", ""),
            "depends_on": list(getattr(node, "depends_on", []) or []),
            "status": getattr(getattr(node, "status", None), "value", getattr(node, "status", "")),
            "result": getattr(node, "result", "") or "",
        })
    return {
        "run_id": getattr(run, "run_id", ""),
        "workflow_name": getattr(workflow, "name", "workflow"),
        "description": getattr(workflow, "description", ""),
        "status": getattr(run, "status", "unknown"),
        "started_at": getattr(run, "started_at", None),
        "finished_at": getattr(run, "finished_at", None),
        "nodes": nodes,
    }


# ------------------------------------------------------------------
# /api/agents
# ------------------------------------------------------------------

def _get_agent_wizard(request: Request) -> AgentWizardService | None:
    factory = getattr(request.app.state, "agent_factory", None)
    if factory is None:
        return None
    wizard = getattr(request.app.state, "agent_wizard_service", None)
    if wizard is None:
        wizard = AgentWizardService(
            factory=factory,
            blueprints_dir=Path("data/agents/blueprints"),
        )
        request.app.state.agent_wizard_service = wizard
    return wizard


def _get_agent_wizard_sessions(request: Request) -> dict[str, dict[str, Any]]:
    sessions = getattr(request.app.state, "agent_wizard_sessions", None)
    if not isinstance(sessions, dict):
        sessions = {}
        request.app.state.agent_wizard_sessions = sessions
    return sessions


def _wizard_steps() -> list[dict[str, Any]]:
    return [
        {"index": 1, "key": "need", "label": "Need"},
        {"index": 2, "key": "blueprint", "label": "Blueprint"},
        {"index": 3, "key": "skills", "label": "Skills & Tools"},
        {"index": 4, "key": "model", "label": "Model & Persona"},
        {"index": 5, "key": "permissions", "label": "Permissions"},
        {"index": 6, "key": "review", "label": "Review & Create"},
    ]


def _wizard_response(session_id: str, need: str, blueprint: AgentBlueprint, *, step: int, phase: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "step": step,
        "phase": phase,
        "steps": _wizard_steps(),
        "need": need,
        "blueprint": blueprint.to_dict(),
    }


async def start_agent_wizard(request: Request) -> JSONResponse:
    """POST /api/agents/wizard/start — initialize the 6-step agent wizard."""
    wizard = _get_agent_wizard(request)
    if wizard is None:
        return JSONResponse({"error": "agent_factory unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    need = str(body.get("need", "")).strip()
    if not need:
        return JSONResponse({"error": "need is required"}, status_code=400)

    analysis = wizard.analyze_request(need)
    blueprint = wizard.generate_blueprint(need)
    session_id = str(uuid.uuid4())
    sessions = _get_agent_wizard_sessions(request)
    sessions[session_id] = {
        "need": need,
        "blueprint": blueprint.to_dict(),
        "step": 2,
        "phase": "review",
    }
    payload = _wizard_response(session_id, need, blueprint, step=2, phase="review")
    payload["analysis"] = analysis
    payload["validation"] = {
        "scenario_count": len(blueprint.test_scenarios),
        "sensitive_permissions": wizard.check_sensitive_permissions(blueprint),
    }
    return JSONResponse(payload, status_code=201)


async def agent_wizard_step(request: Request) -> JSONResponse:
    """POST /api/agents/wizard/step — persist/update/validate/create wizard state."""
    wizard = _get_agent_wizard(request)
    if wizard is None:
        return JSONResponse({"error": "agent_factory unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    session_id = str(body.get("session_id", "")).strip()
    action = str(body.get("action", "update_blueprint")).strip() or "update_blueprint"
    sessions = _get_agent_wizard_sessions(request)
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse({"error": "wizard session not found"}, status_code=404)

    try:
        blueprint_data = body.get("blueprint") or session.get("blueprint") or {}
        blueprint = AgentBlueprint.from_dict(blueprint_data)
    except Exception as exc:
        return JSONResponse({"error": f"invalid blueprint: {exc}"}, status_code=400)

    session["blueprint"] = blueprint.to_dict()
    session["step"] = int(body.get("step") or session.get("step") or 2)
    session["phase"] = str(body.get("phase") or session.get("phase") or "review")
    need = str(session.get("need", ""))

    if action == "validate":
        sensitive = wizard.check_sensitive_permissions(blueprint)
        session["phase"] = "validated"
        payload = _wizard_response(session_id, need, blueprint, step=session["step"], phase="validated")
        payload["validation"] = {
            "scenario_count": len(blueprint.test_scenarios),
            "sensitive_permissions": sensitive,
            "requires_confirmation": bool(sensitive),
        }
        return JSONResponse(payload)

    if action == "create":
        sponsor_confirmed = bool(body.get("sponsor_confirmed", False))
        try:
            runtime = wizard.create_agent(blueprint, sponsor_confirmed=sponsor_confirmed)
        except SensitivePermissionError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "requires_confirmation": True,
                    "sensitive_permissions": exc.sensitive_perms,
                    "blueprint": exc.blueprint.to_dict(),
                },
                status_code=409,
            )
        except Exception as exc:
            logger.exception("Agent wizard create failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        saved_path = wizard.save_blueprint(blueprint)
        sessions.pop(session_id, None)
        return JSONResponse(
            {
                "ok": True,
                "agent_id": runtime.agent_id,
                "blueprint_path": str(saved_path),
                "blueprint": blueprint.to_dict(),
            },
            status_code=201,
        )

    payload = _wizard_response(
        session_id,
        need,
        blueprint,
        step=session["step"],
        phase=str(session.get("phase") or "review"),
    )
    payload["validation"] = {
        "scenario_count": len(blueprint.test_scenarios),
        "sensitive_permissions": wizard.check_sensitive_permissions(blueprint),
    }
    return JSONResponse(payload)

async def list_agents(request: Request) -> JSONResponse:
    """Return all registered agents with their current state."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"agents": [], "total": 0})

    agents = registry.list_all()
    budget_manager = getattr(request.app.state, "budget_manager", None)
    data = []
    for a in agents:
        d = _agent_to_dict(a)
        # A1 — inject token count and cost
        if budget_manager:
            try:
                d["token_count"] = budget_manager.agent_token_count(a.agent_id) if hasattr(budget_manager, "agent_token_count") else 0
                d["cost_usd"] = budget_manager.agent_cost(a.agent_id) if hasattr(budget_manager, "agent_cost") else 0.0
            except Exception:
                d["token_count"] = 0
                d["cost_usd"] = 0.0
        else:
            d["token_count"] = 0
            d["cost_usd"] = 0.0
        data.append(d)
    return JSONResponse({"agents": data, "total": len(data)})


async def get_agent(request: Request) -> JSONResponse:
    """Return a single agent by ID."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry unavailable"}, status_code=503)

    descriptor = registry.get(agent_id)
    if descriptor is None:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    return JSONResponse({"agent": _agent_to_dict(descriptor)})


async def get_agent_state(request: Request) -> JSONResponse:
    """Return the router engine actor states."""
    adapter = getattr(request.app.state, "web_adapter", None)
    if adapter is None:
        return JSONResponse({"actor_states": {}})

    engine = adapter.router_engine
    return JSONResponse({
        "actor_states": engine.actor_states,
        "cycle_in_progress": engine.router_cycle_in_progress,
    })


async def create_agent(request: Request) -> JSONResponse:
    """``POST /api/agents`` — create a new agent via AgentFactory."""
    body = await request.json()
    factory = getattr(request.app.state, "agent_factory", None)
    if factory is None:
        return JSONResponse({"error": "agent_factory unavailable"}, status_code=503)

    from amiagi.domain.agent import AgentDescriptor, AgentRole

    role_str = body.get("role", "executor")
    try:
        role = AgentRole(role_str)
    except (ValueError, KeyError):
        role = AgentRole.EXECUTOR

    agent_id = factory.generate_id()
    descriptor = AgentDescriptor(
        agent_id=agent_id,
        name=body.get("name", agent_id),
        role=role,
        model_name=body.get("model_name", ""),
        model_backend=body.get("model_backend", "ollama"),
        skills=list(body.get("skills", [])),
        persona_prompt=body.get("persona_prompt", ""),
    )

    try:
        factory.create_agent(descriptor)
    except Exception as exc:
        logger.exception("Failed to create agent: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "agent.create", {
        "agent_id": agent_id,
        "name": descriptor.name,
        "role": role_str,
    })
    return JSONResponse({"ok": True, "agent_id": agent_id}, status_code=201)


async def delete_agent(request: Request) -> JSONResponse:
    """``DELETE /api/agents/{agent_id}`` — remove an agent."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry unavailable"}, status_code=503)

    descriptor = registry.get(agent_id)
    if descriptor is None:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    removed = False
    if hasattr(registry, "remove"):
        removed = registry.remove(agent_id)
    elif hasattr(registry, "unregister"):
        removed = registry.unregister(agent_id)
    elif hasattr(registry, "delete"):
        removed = registry.delete(agent_id)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "agent.delete", {"agent_id": agent_id})
    return JSONResponse({"ok": True, "deleted": removed})


# ------------------------------------------------------------------
# /api/tasks
# ------------------------------------------------------------------

async def list_tasks(request: Request) -> JSONResponse:
    """Return all known tasks.

    Query params:
    - ``origin``: filter by task origin (``operator`` or ``system``)
    """
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"tasks": [], "total": 0})

    tasks = _list_task_objects(task_queue)

    data = []
    for t in tasks:
        try:
            data.append(_task_to_dict(t))
        except Exception:
            logger.debug("Skipping non-serialisable task: %s", t)

    # Optional filters
    origin_filter = request.query_params.get("origin")
    if origin_filter:
        data = [d for d in data if d.get("origin") == origin_filter]

    # T8 — Additional filters: status, agent, priority
    status_filter = request.query_params.get("status")
    if status_filter:
        data = [d for d in data if d.get("status") == status_filter]

    agent_filter = request.query_params.get("agent")
    if agent_filter:
        data = [d for d in data if d.get("assigned_agent_id") == agent_filter]

    priority_filter = request.query_params.get("priority")
    if priority_filter:
        data = [d for d in data if d.get("priority") == priority_filter]

    search = (request.query_params.get("q") or "").strip().lower()
    if search:
        data = [
            d for d in data
            if search in (d.get("title") or "").lower()
            or search in (d.get("description") or "").lower()
            or search in (d.get("task_id") or "").lower()
            or search in (d.get("assigned_agent_id") or "").lower()
            or search in str(d.get("metadata") or "").lower()
        ]

    since = _parse_since(request.query_params.get("since"))
    if since is not None:
        data = [
            d for d in data
            if d.get("created_at") and datetime.fromisoformat(d["created_at"]).astimezone(timezone.utc) >= since
        ]

    return JSONResponse({"tasks": data, "total": len(data)})


async def get_task(request: Request) -> JSONResponse:
    """Return a single task with full detail for the task drawer."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"error": "task_queue unavailable"}, status_code=503)

    task = _get_task_object(task_queue, task_id)

    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    return JSONResponse({"task": _task_to_dict(task)})


async def create_task(request: Request) -> JSONResponse:
    """``POST /api/tasks`` — create a new task and log to audit trail."""
    body = await request.json()
    task_queue = getattr(request.app.state, "task_queue", None)

    task_data = {
        "title": body.get("title", "Untitled"),
        "description": body.get("description", ""),
        "priority": body.get("priority", "medium"),
        "origin": body.get("origin", "operator"),
        "assigned_agent_id": body.get("assigned_agent_id"),
    }

    task_id = None
    if task_queue is not None and hasattr(task_queue, "add"):
        task_id = task_queue.add(**task_data)
    elif task_queue is not None:
        from amiagi.domain.task import Task, TaskPriority

        priority_raw = str(task_data["priority"] or "normal").lower()
        try:
            priority = TaskPriority(priority_raw)
        except ValueError:
            priority = TaskPriority.NORMAL

        task = Task(
            task_id=Task.generate_id(),
            title=task_data["title"],
            description=task_data["description"],
            priority=priority,
            assigned_agent_id=task_data.get("assigned_agent_id"),
            metadata={"origin": task_data["origin"]},
        )
        if hasattr(task_queue, "enqueue"):
            task_queue.enqueue(task)
            task_id = task.task_id

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.create", {
        "task_id": str(task_id) if task_id else None,
        "title": task_data["title"],
    })
    await _broadcast_web_event(request, "task.created", {
        "task_id": str(task_id) if task_id else None,
        "title": task_data["title"],
        "status": "pending",
        "assigned_agent_id": task_data.get("assigned_agent_id"),
    })
    return JSONResponse({"ok": True, "task_id": str(task_id) if task_id else None}, status_code=201)


async def cancel_task(request: Request) -> JSONResponse:
    """``POST /api/tasks/{task_id}/cancel`` — cancel a task and log to audit trail."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)

    cancelled = False
    if task_queue is not None:
        if hasattr(task_queue, "cancel"):
            try:
                result = task_queue.cancel(task_id)
                cancelled = True if result is None else bool(result)
            except KeyError:
                cancelled = False
        elif hasattr(task_queue, "remove"):
            try:
                task_queue.remove(task_id)
                cancelled = True
            except KeyError:
                cancelled = False

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.cancel", {"task_id": task_id, "cancelled": cancelled})
    if cancelled:
        await _broadcast_web_event(request, "task.cancelled", {"task_id": task_id, "status": "cancelled"})
    return JSONResponse({"ok": cancelled, "task_id": task_id})


async def bulk_update_tasks(request: Request) -> JSONResponse:
    """POST /api/tasks/bulk — bulk cancel, reassign, or escalate tasks."""
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"error": "task_queue unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    task_ids = body.get("task_ids") or []
    action = str(body.get("action") or "").strip().lower()
    if not isinstance(task_ids, list) or not task_ids:
        return JSONResponse({"error": "task_ids required"}, status_code=400)
    if action not in {"cancel", "reassign", "escalate"}:
        return JSONResponse({"error": "invalid action"}, status_code=400)

    updated: list[str] = []
    agent_id = body.get("agent_id")
    for task_id in task_ids:
        task = _get_task_object(task_queue, str(task_id))
        if task is None:
            continue
        if action == "cancel":
            if not getattr(task, "is_terminal", False):
                if hasattr(task, "cancel"):
                    task.cancel()
                updated.append(str(task_id))
        elif action == "reassign":
            task.assigned_agent_id = agent_id
            updated.append(str(task_id))
        elif action == "escalate":
            from amiagi.domain.task import TaskPriority

            current = str(getattr(getattr(task, "priority", None), "value", getattr(task, "priority", "normal")))
            task.priority = TaskPriority.CRITICAL if current != TaskPriority.CRITICAL.value else TaskPriority.HIGH
            updated.append(str(task_id))

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, f"task.bulk_{action}", {"task_ids": updated, "agent_id": agent_id})
    if updated:
        await _broadcast_web_event(request, "task.bulk_updated", {
            "task_ids": updated,
            "action": action,
            "agent_id": agent_id,
        })
    return JSONResponse({"ok": True, "action": action, "updated": updated, "count": len(updated)})


async def get_task_subtasks(request: Request) -> JSONResponse:
    """GET /api/tasks/{task_id}/subtasks — return child tasks."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"subtasks": [], "total": 0})

    tasks = _list_task_objects(task_queue)

    subtasks = [_task_to_dict(t) for t in tasks if getattr(t, "parent_task_id", None) == task_id]
    return JSONResponse({"subtasks": subtasks, "total": len(subtasks)})


async def decompose_task(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/decompose — generate and enqueue subtasks."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"error": "task_queue unavailable"}, status_code=503)

    task = _get_task_object(task_queue, task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    existing = [t for t in _list_task_objects(task_queue) if getattr(t, "parent_task_id", None) == task_id]
    if existing:
        return JSONResponse({
            "ok": True,
            "task_id": task_id,
            "subtasks": [_task_to_dict(t) for t in existing],
            "created": 0,
        })

    from amiagi.application.task_decomposer import TaskDecomposer

    decomposer = getattr(request.app.state, "task_decomposer", None) or TaskDecomposer()
    subtasks = decomposer.decompose(task)
    created: list[dict[str, Any]] = []
    for subtask in subtasks:
        if hasattr(task_queue, "enqueue"):
            task_queue.enqueue(subtask)
            created.append(_task_to_dict(subtask))

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.decompose", {"task_id": task_id, "created": len(created)})
    if created:
        await _broadcast_web_event(request, "task.decomposed", {
            "task_id": task_id,
            "created": len(created),
        })
    return JSONResponse({"ok": True, "task_id": task_id, "subtasks": created, "created": len(created)})


async def reassign_task(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/reassign — reassign task to another agent."""
    task_id = request.path_params["task_id"]
    body = await request.json()
    agent_id = body.get("agent_id")
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"error": "task_queue unavailable"}, status_code=503)

    task = _get_task_object(task_queue, task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    task.assigned_agent_id = agent_id
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.reassign", {"task_id": task_id, "agent_id": agent_id})
    await _broadcast_web_event(request, "task.reassigned", {
        "task_id": task_id,
        "agent_id": agent_id,
        "status": str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "pending"))),
    })
    return JSONResponse({"ok": True, "task_id": task_id, "agent_id": agent_id})


async def move_task(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/move — move task between Kanban status columns."""
    task_id = request.path_params["task_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    status_raw = str(body.get("status") or "").strip().lower()
    if not status_raw:
        return JSONResponse({"error": "status required"}, status_code=400)

    from amiagi.domain.task import TaskStatus

    try:
        new_status = TaskStatus(status_raw)
    except ValueError:
        return JSONResponse({"error": "invalid status"}, status_code=400)

    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"error": "task_queue unavailable"}, status_code=503)

    task = _get_task_object(task_queue, task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    old_status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "pending")))
    now = datetime.now(timezone.utc)

    if new_status == TaskStatus.PENDING:
        task.status = TaskStatus.PENDING
        task.started_at = None
        task.completed_at = None
    elif new_status == TaskStatus.ASSIGNED:
        task.status = TaskStatus.ASSIGNED
        task.completed_at = None
    elif new_status == TaskStatus.IN_PROGRESS:
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = task.started_at or now
        task.completed_at = None
    elif new_status == TaskStatus.REVIEW:
        task.status = TaskStatus.REVIEW
        task.started_at = task.started_at or now
        task.completed_at = None
    elif new_status == TaskStatus.DONE:
        task.status = TaskStatus.DONE
        task.started_at = task.started_at or now
        task.completed_at = task.completed_at or now
    elif new_status == TaskStatus.FAILED:
        task.status = TaskStatus.FAILED
        task.started_at = task.started_at or now
        task.completed_at = task.completed_at or now
    elif new_status == TaskStatus.CANCELLED:
        task.cancel()

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.move", {
        "task_id": task_id,
        "from_status": old_status,
        "to_status": new_status.value,
    })
    await _broadcast_web_event(request, "task.moved", {
        "task_id": task_id,
        "from_status": old_status,
        "status": new_status.value,
        "to_status": new_status.value,
        "assigned_agent_id": getattr(task, "assigned_agent_id", None),
    })
    return JSONResponse({
        "ok": True,
        "task_id": task_id,
        "from_status": old_status,
        "status": new_status.value,
        "task": _task_to_dict(task),
    })


async def get_task_workflow(request: Request) -> JSONResponse:
    """GET /api/tasks/{task_id}/workflow — resolve workflow run linked to task metadata."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)
    task = _get_task_object(task_queue, task_id)
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    metadata = getattr(task, "metadata", None) or {}
    workflow_engine = getattr(request.app.state, "workflow_engine", None)
    if workflow_engine is None:
        return JSONResponse({"workflow": None, "task_id": task_id})

    run_id = metadata.get("workflow_run_id") or metadata.get("run_id")
    run = workflow_engine.get_run(run_id) if run_id and hasattr(workflow_engine, "get_run") else None

    if run is None and hasattr(workflow_engine, "list_runs"):
        workflow_name = metadata.get("workflow_name")
        for item in workflow_engine.list_runs():
            if workflow_name and getattr(getattr(item, "workflow", None), "name", None) == workflow_name:
                run = item
                break
            if task_id and metadata.get("task_id") == task_id:
                run = item
                break

    return JSONResponse({
        "task_id": task_id,
        "workflow": _workflow_run_to_dict(run) if run is not None else None,
    })


async def get_task_stats(request: Request) -> JSONResponse:
    """GET /api/tasks/stats — aggregate task statistics."""
    task_queue = getattr(request.app.state, "task_queue", None)
    if task_queue is None:
        return JSONResponse({"total": 0, "by_status": {}, "by_priority": {}})

    tasks = _list_task_objects(task_queue)

    by_status: dict = {}
    by_priority: dict = {}
    for t in tasks:
        s = str(getattr(t.status, "value", t.status))
        p = str(getattr(t.priority, "value", t.priority))
        by_status[s] = by_status.get(s, 0) + 1
        by_priority[p] = by_priority.get(p, 0) + 1

    avg_completion_time_seconds = _avg_completion_seconds(tasks)

    return JSONResponse({
        "total": len(tasks),
        "by_status": by_status,
        "by_priority": by_priority,
        "avg_completion_time_seconds": avg_completion_time_seconds,
        "avg_completion_time_label": _format_duration(avg_completion_time_seconds),
    })


# ------------------------------------------------------------------
# /api/metrics
# ------------------------------------------------------------------

async def get_metrics(request: Request) -> JSONResponse:
    """Return collected metrics from the MetricsCollector."""
    collector = getattr(request.app.state, "metrics_collector", None)
    if collector is None:
        return JSONResponse({"metrics": {}})

    metrics: dict[str, Any] = {}
    if hasattr(collector, "summary"):
        metrics = collector.summary()
    elif hasattr(collector, "to_dict"):
        metrics = collector.to_dict()
    elif hasattr(collector, "snapshot"):
        metrics = collector.snapshot()

    return JSONResponse({"metrics": metrics})


# ------------------------------------------------------------------
# /api/budget
# ------------------------------------------------------------------

async def get_budget(request: Request) -> JSONResponse:
    """Return budget summaries (per-agent, per-task, session)."""
    bm = getattr(request.app.state, "budget_manager", None)
    if bm is None:
        return JSONResponse({"agents": {}, "tasks": {}, "session": {}})

    return JSONResponse({
        "agents": bm.summary(),
        "tasks": bm.task_summary(),
        "session": bm.session_summary(),
    })


async def get_budget_tasks(request: Request) -> JSONResponse:
    """``GET /api/budget/tasks`` — detailed per-task cost breakdown."""
    bm = getattr(request.app.state, "budget_manager", None)
    if bm is None:
        return JSONResponse({"tasks": {}})

    return JSONResponse({"tasks": bm.task_summary()})


async def get_budget_config(request: Request) -> JSONResponse:
    """``GET /api/budget/config`` — return cost configuration."""
    bm = getattr(request.app.state, "budget_manager", None)
    config: dict[str, Any] = {}
    if bm is not None:
        config = {
            "currency": getattr(bm, "currency", "USD"),
            "energy_price_kwh": getattr(bm, "energy_price_kwh", 0.0),
            "token_cost_1k": getattr(bm, "token_cost_1k", 0.0),
        }
    return JSONResponse(config)


async def update_budget_config(request: Request) -> JSONResponse:
    """``PUT /api/budget/config`` — update cost configuration."""
    body = await request.json()
    bm = getattr(request.app.state, "budget_manager", None)
    if bm is not None:
        if "currency" in body:
            bm.currency = body["currency"]
        if "energy_price_kwh" in body:
            bm.energy_price_kwh = float(body["energy_price_kwh"])
        if "token_cost_1k" in body:
            bm.token_cost_1k = float(body["token_cost_1k"])

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "budget.config.update", body)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------
# Status bar (real-time telemetry for the bottom status bar)
# ------------------------------------------------------------------

async def get_status_bar(request: Request) -> JSONResponse:
    """GET /api/status-bar — lightweight endpoint for JS status-bar polling.

    Returns model status, budget, task counts, uptime in a single call.
    """
    import time as _time
    state = request.app.state
    result: dict = {}

    # ── Model name (cached, avoids sync file I/O on every poll) ──
    result["model_name"] = _get_model_name_cached()

    # ── Ollama connectivity (cached 30s, avoids HTTP timeout pressure) ──
    alive, _ = await _check_ollama_cached()
    result["model_alive"] = alive

    # ── Budget (session) ──
    budget_mgr = getattr(state, "budget_manager", None)
    if budget_mgr is not None:
        try:
            sb = budget_mgr.session_budget
            result["budget_pct"] = round(sb.utilization_pct, 1)
            result["budget_used"] = f"{sb.spent_usd:.2f}"
            lim = sb.limit_usd
            result["budget_limit"] = f"{lim:.2f}" if lim > 0 else "∞"
            result["token_count"] = sb.tokens_used
        except Exception:
            result["budget_pct"] = 0
            result["budget_used"] = "0.00"
            result["budget_limit"] = "∞"
            result["token_count"] = 0
    else:
        result["budget_pct"] = 0
        result["budget_used"] = "0.00"
        result["budget_limit"] = "∞"
        result["token_count"] = 0

    # ── Active tasks ──
    result.update(_get_status_bar_task_counts(getattr(state, "task_queue", None)))

    # ── Inbox (pending HITL inbox items) ──
    inbox_svc = getattr(state, "inbox_service", None)
    if inbox_svc is not None:
        try:
            counts = await inbox_svc.count_by_status()
            result["inbox_pending"] = int(counts.get("pending", 0))
        except Exception:
            result["inbox_pending"] = 0
    else:
        notif_svc = getattr(state, "notification_service", None)
        if notif_svc is not None:
            try:
                user = getattr(request.state, "user", None)
                user_id = str(getattr(user, "user_id", "anonymous")) if user is not None else "anonymous"
                result["inbox_pending"] = await notif_svc.unread_count(user_id)
            except Exception:
                result["inbox_pending"] = 0
        else:
            result["inbox_pending"] = 0

    # ── Uptime ──
    startup_ts = getattr(state, "_startup_time", None)
    if startup_ts is not None:
        elapsed = int(_time.time() - startup_ts)
        if elapsed < 60:
            result["uptime"] = f"{elapsed}s"
        elif elapsed < 3600:
            result["uptime"] = f"{elapsed // 60}m"
        else:
            h, m = divmod(elapsed // 60, 60)
            result["uptime"] = f"{h}h {m}m"
    else:
        result["uptime"] = "0m"

    return JSONResponse(result)


# ------------------------------------------------------------------
# Route table
# ------------------------------------------------------------------

api_routes: list[Route] = [
    Route("/api/agents/state", get_agent_state, methods=["GET"]),
    Route("/api/agents/wizard/start", start_agent_wizard, methods=["POST"]),
    Route("/api/agents/wizard/step", agent_wizard_step, methods=["POST"]),
    Route("/api/agents/{agent_id}", get_agent, methods=["GET"]),
    Route("/api/agents/{agent_id}", delete_agent, methods=["DELETE"]),
    Route("/api/agents", list_agents, methods=["GET"]),
    Route("/api/agents", create_agent, methods=["POST"]),
    Route("/api/tasks/stats", get_task_stats, methods=["GET"]),
    Route("/api/tasks/bulk", bulk_update_tasks, methods=["POST"]),
    Route("/api/tasks/{task_id}/subtasks", get_task_subtasks, methods=["GET"]),
    Route("/api/tasks/{task_id}/decompose", decompose_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/workflow", get_task_workflow, methods=["GET"]),
    Route("/api/tasks/{task_id}/move", move_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/reassign", reassign_task, methods=["POST"]),
    Route("/api/tasks/{task_id}/cancel", cancel_task, methods=["POST"]),
    Route("/api/tasks/{task_id}", get_task, methods=["GET"]),
    Route("/api/tasks", list_tasks, methods=["GET"]),
    Route("/api/tasks", create_task, methods=["POST"]),
    Route("/api/metrics", get_metrics, methods=["GET"]),
    Route("/api/budget", get_budget, methods=["GET"]),
    Route("/api/budget/tasks", get_budget_tasks, methods=["GET"]),
    Route("/api/budget/config", get_budget_config, methods=["GET"]),
    Route("/api/budget/config", update_budget_config, methods=["PUT"]),
    Route("/api/status-bar", get_status_bar, methods=["GET"]),
]
