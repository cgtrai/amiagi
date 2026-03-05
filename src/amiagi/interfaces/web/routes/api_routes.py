"""REST API routes for agents, tasks, metrics, and budget.

Provides JSON endpoints consumed by the dashboard and web components.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


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
        "origin": origin,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "metadata": metadata,
    }


# ------------------------------------------------------------------
# /api/agents
# ------------------------------------------------------------------

async def list_agents(request: Request) -> JSONResponse:
    """Return all registered agents with their current state."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"agents": [], "total": 0})

    agents = registry.list_all()
    data = [_agent_to_dict(a) for a in agents]
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

    # TaskQueue exposes .list_all() or ._tasks or similar
    tasks: list[Any] = []
    if hasattr(task_queue, "list_all"):
        tasks = task_queue.list_all()
    elif hasattr(task_queue, "all_tasks"):
        tasks = task_queue.all_tasks()
    elif hasattr(task_queue, "_tasks"):
        tasks = list(task_queue._tasks.values()) if isinstance(task_queue._tasks, dict) else list(task_queue._tasks)

    data = []
    for t in tasks:
        try:
            data.append(_task_to_dict(t))
        except Exception:
            logger.debug("Skipping non-serialisable task: %s", t)

    # Optional origin filter
    origin_filter = request.query_params.get("origin")
    if origin_filter:
        data = [d for d in data if d.get("origin") == origin_filter]

    return JSONResponse({"tasks": data, "total": len(data)})


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

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.create", {
        "task_id": str(task_id) if task_id else None,
        "title": task_data["title"],
    })
    return JSONResponse({"ok": True, "task_id": str(task_id) if task_id else None}, status_code=201)


async def cancel_task(request: Request) -> JSONResponse:
    """``POST /api/tasks/{task_id}/cancel`` — cancel a task and log to audit trail."""
    task_id = request.path_params["task_id"]
    task_queue = getattr(request.app.state, "task_queue", None)

    cancelled = False
    if task_queue is not None:
        if hasattr(task_queue, "cancel"):
            cancelled = task_queue.cancel(task_id)
        elif hasattr(task_queue, "remove"):
            cancelled = task_queue.remove(task_id)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "task.cancel", {"task_id": task_id, "cancelled": cancelled})
    return JSONResponse({"ok": cancelled, "task_id": task_id})


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


# ------------------------------------------------------------------
# Route table
# ------------------------------------------------------------------

api_routes: list[Route] = [
    Route("/api/agents/state", get_agent_state, methods=["GET"]),
    Route("/api/agents/{agent_id}", get_agent, methods=["GET"]),
    Route("/api/agents", list_agents, methods=["GET"]),
    Route("/api/tasks/{task_id}/cancel", cancel_task, methods=["POST"]),
    Route("/api/tasks", list_tasks, methods=["GET"]),
    Route("/api/tasks", create_task, methods=["POST"]),
    Route("/api/metrics", get_metrics, methods=["GET"]),
    Route("/api/budget", get_budget, methods=["GET"]),
    Route("/api/budget/tasks", get_budget_tasks, methods=["GET"]),
]
