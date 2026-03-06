"""REST API routes for agents, tasks, metrics, and budget.

Provides JSON endpoints consumed by the dashboard and web components.
"""

from __future__ import annotations

import logging
import time as _time_module
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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
    task_queue = getattr(state, "task_queue", None)
    if task_queue is not None:
        try:
            result["active_tasks"] = task_queue.pending_count()
        except Exception:
            result["active_tasks"] = 0
    else:
        result["active_tasks"] = 0

    # ── Inbox (unread notification count) ──
    notif_svc = getattr(state, "notification_service", None)
    if notif_svc is not None:
        try:
            result["inbox_pending"] = await notif_svc.unread_count()
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
    Route("/api/agents/{agent_id}", get_agent, methods=["GET"]),
    Route("/api/agents", list_agents, methods=["GET"]),
    Route("/api/agents", create_agent, methods=["POST"]),
    Route("/api/tasks/{task_id}/cancel", cancel_task, methods=["POST"]),
    Route("/api/tasks", list_tasks, methods=["GET"]),
    Route("/api/tasks", create_task, methods=["POST"]),
    Route("/api/metrics", get_metrics, methods=["GET"]),
    Route("/api/budget", get_budget, methods=["GET"]),
    Route("/api/budget/tasks", get_budget_tasks, methods=["GET"]),
    Route("/api/budget/config", get_budget_config, methods=["GET"]),
    Route("/api/budget/config", update_budget_config, methods=["PUT"]),
    Route("/api/status-bar", get_status_bar, methods=["GET"]),
]
