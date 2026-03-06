"""Inbox (Human-in-the-Loop) API routes.

Endpoints:
    GET    /api/inbox          — paginated list with status filter
    GET    /api/inbox/count    — pending count (lightweight, for badge)
    GET    /api/inbox/{id}     — single item detail
    POST   /api/inbox/{id}/approve  — approve an item
    POST   /api/inbox/{id}/reject   — reject an item
    POST   /api/inbox/{id}/reply    — reply with free-form message
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _get_inbox(request: Request):
    return getattr(request.app.state, "inbox_service", None)


def _no_service() -> JSONResponse:
    return JSONResponse({"error": "inbox_service unavailable"}, status_code=503)


# ── Endpoints ────────────────────────────────────────────────

async def inbox_list(request: Request) -> JSONResponse:
    """GET /api/inbox?status=pending&limit=50&offset=0"""
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    status = request.query_params.get("status")
    limit = min(int(request.query_params.get("limit", "50")), 200)
    offset = int(request.query_params.get("offset", "0"))

    items = await svc.list_items(status=status, limit=limit, offset=offset)
    return JSONResponse({
        "items": [i.to_dict() for i in items],
        "total": len(items),
    })


async def inbox_count(request: Request) -> JSONResponse:
    """GET /api/inbox/count"""
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    counts = await svc.count_by_status()
    return JSONResponse({
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "total": sum(counts.values()),
    })


async def inbox_detail(request: Request) -> JSONResponse:
    """GET /api/inbox/{item_id}"""
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    item = await svc.get(request.path_params["item_id"])
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse({"item": item.to_dict()})


async def inbox_approve(request: Request) -> JSONResponse:
    """POST /api/inbox/{item_id}/approve

    If the item comes from a workflow gate, also advances the workflow.
    """
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    item_id = request.path_params["item_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    resolved_by = body.get("resolved_by", "operator")
    item = await svc.approve(item_id, resolved_by=resolved_by)
    if item is None:
        return JSONResponse({"error": "not found or already resolved"}, status_code=404)

    # If source_type is "workflow" and we have source_id + node_id, approve the gate
    if item.source_type == "workflow" and item.source_id and item.node_id:
        adapter = getattr(request.app.state, "web_adapter", None)
        if adapter is not None:
            engine = getattr(adapter, "router_engine", None)
            wf_engine = getattr(engine, "workflow_engine", None) if engine else None
            if wf_engine is None:
                wf_engine = getattr(request.app.state, "workflow_engine", None)
            if wf_engine is not None:
                try:
                    wf_engine.approve_gate(item.source_id, item.node_id)
                    logger.info("Gate approved: workflow=%s node=%s", item.source_id, item.node_id)
                except Exception:
                    logger.exception("Failed to approve workflow gate %s/%s", item.source_id, item.node_id)

    # Broadcast event
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "resolution": "approved",
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.approve", {"item_id": item_id})
    return JSONResponse({"ok": True, "item": item.to_dict()})


async def inbox_reject(request: Request) -> JSONResponse:
    """POST /api/inbox/{item_id}/reject"""
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    item_id = request.path_params["item_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    resolved_by = body.get("resolved_by", "operator")
    reason = body.get("reason", "")
    item = await svc.reject(item_id, resolved_by=resolved_by, reason=reason)
    if item is None:
        return JSONResponse({"error": "not found or already resolved"}, status_code=404)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "resolution": "rejected",
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.reject", {"item_id": item_id, "reason": reason})
    return JSONResponse({"ok": True, "item": item.to_dict()})


async def inbox_reply(request: Request) -> JSONResponse:
    """POST /api/inbox/{item_id}/reply  — free-form text reply.

    The reply text is stored as the ``resolution`` field and the item
    is resolved as ``approved`` (reply implies the human provided an answer).
    """
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    item_id = request.path_params["item_id"]
    body = await request.json()
    message = body.get("message", "")
    resolved_by = body.get("resolved_by", "operator")

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    item = await svc._resolve(item_id, "approved", resolved_by, reason=message)
    if item is None:
        return JSONResponse({"error": "not found or already resolved"}, status_code=404)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "resolution": "replied",
            "message": message,
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.reply", {"item_id": item_id})
    return JSONResponse({"ok": True, "item": item.to_dict()})


# ── Agent lifecycle endpoints ────────────────────────────────
# These could live in a separate file but logically belong to
# Sprint P1's "operator controls" capability.

async def agent_pause(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/pause"""
    return await _agent_lifecycle(request, "pause")


async def agent_resume(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/resume"""
    return await _agent_lifecycle(request, "resume")


async def agent_terminate(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/terminate"""
    return await _agent_lifecycle(request, "terminate")


async def _agent_lifecycle(request: Request, action: str) -> JSONResponse:
    """Dispatch pause/resume/terminate to the AgentRuntime."""
    agent_id = request.path_params["agent_id"]

    # Try to find the runtime instance via web_adapter → router_engine
    adapter = getattr(request.app.state, "web_adapter", None)
    engine = getattr(adapter, "router_engine", None) if adapter else None

    # The router_engine keeps a dict of runtimes (agent_id → AgentRuntime)
    runtime = None
    if engine is not None:
        runtimes = getattr(engine, "_runtimes", None) or getattr(engine, "runtimes", None)
        if isinstance(runtimes, dict):
            runtime = runtimes.get(agent_id)

    # Also try the registry for state validation
    registry = getattr(request.app.state, "agent_registry", None)

    if runtime is None and registry is None:
        return JSONResponse({"error": "No runtime or registry available"}, status_code=503)

    try:
        if runtime is not None:
            method = getattr(runtime, action, None)
            if method is None:
                return JSONResponse({"error": f"action '{action}' not supported"}, status_code=400)
            method()  # pause(), resume(), terminate() are synchronous
        elif registry is not None:
            from amiagi.domain.agent import AgentState
            state_map = {
                "pause": AgentState.PAUSED,
                "resume": AgentState.IDLE,
                "terminate": AgentState.TERMINATED,
            }
            new_state = state_map.get(action)
            if new_state is None:
                return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
            registry.update_state(agent_id, new_state, reason=f"operator:{action}")
    except Exception as exc:
        logger.exception("Agent lifecycle %s failed for %s", action, agent_id)
        return JSONResponse({"error": str(exc)}, status_code=409)

    # Broadcast state change
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("agent.lifecycle", {
            "agent_id": agent_id,
            "action": action,
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, f"agent.{action}", {"agent_id": agent_id})

    # Return fresh agent state
    descriptor = registry.get(agent_id) if registry else None
    state_str = str(descriptor.state.value) if descriptor and hasattr(descriptor.state, "value") else action
    return JSONResponse({"ok": True, "agent_id": agent_id, "state": state_str})


# ── Route table ──────────────────────────────────────────────

inbox_routes: list[Route] = [
    Route("/api/inbox/count", inbox_count, methods=["GET"]),
    Route("/api/inbox/{item_id}/approve", inbox_approve, methods=["POST"]),
    Route("/api/inbox/{item_id}/reject", inbox_reject, methods=["POST"]),
    Route("/api/inbox/{item_id}/reply", inbox_reply, methods=["POST"]),
    Route("/api/inbox/{item_id}", inbox_detail, methods=["GET"]),
    Route("/api/inbox", inbox_list, methods=["GET"]),
    # Agent lifecycle
    Route("/api/agents/{agent_id}/pause", agent_pause, methods=["POST"]),
    Route("/api/agents/{agent_id}/resume", agent_resume, methods=["POST"]),
    Route("/api/agents/{agent_id}/terminate", agent_terminate, methods=["POST"]),
]
