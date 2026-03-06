"""System-level and extended operator API routes.

Endpoints:
    GET   /api/system/state        — consolidated system state (2.3)
    POST  /api/system/input        — operator text input / command (2.4)
    POST  /api/inbox/{id}/delegate — delegate inbox item to agent (2.9)
    POST  /api/agents/spawn        — spawn a new agent instance (2.11)
"""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


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
    if task_queue is not None:
        try:
            result["tasks"] = {
                "pending": task_queue.pending_count(),
                "total": task_queue.total_count() if hasattr(task_queue, "total_count") else task_queue.pending_count(),
            }
        except Exception:
            result["tasks"] = {"pending": 0, "total": 0}
    else:
        result["tasks"] = {"pending": 0, "total": 0}

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

    return JSONResponse(result)


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

    engine = getattr(adapter, "router_engine", None)
    if engine is None:
        return JSONResponse({"error": "router_engine unavailable"}, status_code=503)

    try:
        # RouterEngine.handle_input() accepts user message + optional target
        handle = getattr(engine, "handle_input", None) or getattr(engine, "send_message", None)
        if handle is None:
            return JSONResponse({"error": "router_engine has no input handler"}, status_code=501)

        if target_agent:
            result = handle(message, target_agent=target_agent)
        else:
            result = handle(message)

        # Log the operator action
        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "system.input", {
            "message": message[:200],
            "target_agent": target_agent,
        })

        return JSONResponse({
            "ok": True,
            "response": str(result) if result else "accepted",
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
    agent_id = (body.get("agent_id") or "").strip()
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
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("inbox.delegated", {
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
        hub = getattr(request.app.state, "event_hub", None)
        if hub is not None:
            hub.broadcast("agent.spawned", {
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
    Route("/api/system/input", system_input, methods=["POST"]),
    Route("/api/inbox/{item_id}/delegate", inbox_delegate, methods=["POST"]),
    Route("/api/agents/spawn", agent_spawn, methods=["POST"]),
]
