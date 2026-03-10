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
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _get_inbox(request: Request):
    return getattr(request.app.state, "inbox_service", None)


def _no_service() -> JSONResponse:
    return JSONResponse({"error": "inbox_service unavailable"}, status_code=503)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


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
        "expired": counts.get("expired", 0),
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
        await hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "item_type": item.item_type,
            "agent_id": item.agent_id,
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
        await hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "item_type": item.item_type,
            "agent_id": item.agent_id,
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
        await hub.broadcast("inbox.resolved", {
            "item_id": item.id,
            "item_type": item.item_type,
            "agent_id": item.agent_id,
            "resolution": "replied",
            "message": message,
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.reply", {"item_id": item_id})
    return JSONResponse({"ok": True, "item": item.to_dict()})


async def _batch_resolve(request: Request, action: str) -> JSONResponse:
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    body = await _json_body(request)
    item_ids = body.get("item_ids") or body.get("ids") or []
    if not isinstance(item_ids, list) or not item_ids:
        return JSONResponse({"error": "item_ids required"}, status_code=400)

    method = getattr(svc, action, None)
    if method is None:
        return JSONResponse({"error": f"unsupported action: {action}"}, status_code=400)

    resolved_items = []
    failed_ids = []
    for item_id in item_ids:
        if not item_id:
            continue
        kwargs = {"resolved_by": body.get("resolved_by", "operator")}
        if action == "reject":
            kwargs["reason"] = body.get("reason", "")
        item = await method(str(item_id), **kwargs)
        if item is None:
            failed_ids.append(str(item_id))
            continue
        resolved_items.append(item.to_dict())

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None and resolved_items:
        await hub.broadcast("inbox.batch_resolved", {
            "resolution": "approved" if action == "approve" else "rejected",
            "item_ids": [item["id"] for item in resolved_items],
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, f"inbox.batch_{action}", {
        "count": len(resolved_items),
        "failed_ids": failed_ids,
    })

    return JSONResponse({
        "ok": True,
        "resolved": resolved_items,
        "resolved_count": len(resolved_items),
        "failed_ids": failed_ids,
    })


async def inbox_batch_approve(request: Request) -> JSONResponse:
    """POST /api/inbox/batch/approve"""
    return await _batch_resolve(request, "approve")


async def inbox_batch_reject(request: Request) -> JSONResponse:
    """POST /api/inbox/batch/reject"""
    return await _batch_resolve(request, "reject")


async def inbox_grant_secret(request: Request) -> JSONResponse:
    """POST /api/inbox/{item_id}/grant.

    Resolves a pending ``secret_request`` item and binds the referenced vault
    secret to the requested entity (currently agent/skill assignments).
    """
    svc = _get_inbox(request)
    if svc is None:
        return _no_service()

    item_id = request.path_params["item_id"]
    item = await svc.get(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if item.status != "pending":
        return JSONResponse({"error": "not found or already resolved"}, status_code=404)
    if item.item_type != "secret_request":
        return JSONResponse({"error": "not a secret_request item"}, status_code=400)

    body = await _json_body(request)
    metadata = item.metadata or {}
    secret_id = str(body.get("secret_id") or metadata.get("secret_id") or "").strip()
    entity_type = str(body.get("entity_type") or metadata.get("entity_type") or "agent").strip() or "agent"
    entity_id = str(
        body.get("entity_id")
        or body.get("agent_id")
        or metadata.get("entity_id")
        or metadata.get("agent_id")
        or item.agent_id
        or ""
    ).strip()

    if not secret_id or not entity_id:
        return JSONResponse({"error": "secret_id and entity_id required"}, status_code=400)

    try:
        from amiagi.interfaces.web.routes.vault_routes import _parse_secret_id

        secret_agent_id, secret_key = _parse_secret_id(secret_id)
    except ValueError:
        return JSONResponse({"error": "invalid_secret_id"}, status_code=400)

    vault = getattr(request.app.state, "secret_vault", None)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    existing = await vault.aget_secret(secret_agent_id, secret_key)
    if existing is None:
        return JSONResponse({"error": "secret_not_found"}, status_code=404)

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse({"error": "db_not_available"}, status_code=503)

    user = getattr(request.state, "user", None)
    user_id = str(user.user_id) if user else "operator"

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """SELECT entity_type, entity_id FROM dbo.vault_assignments
                       WHERE secret_agent_id = $1 AND secret_key = $2""",
                    secret_agent_id,
                    secret_key,
                )
                assignments = {(row["entity_type"], row["entity_id"]) for row in rows}
                assignments.add((entity_type, entity_id))
                await conn.execute(
                    """DELETE FROM dbo.vault_assignments
                       WHERE secret_agent_id = $1 AND secret_key = $2""",
                    secret_agent_id,
                    secret_key,
                )
                for assigned_type, assigned_id in sorted(assignments):
                    await conn.execute(
                        """INSERT INTO dbo.vault_assignments
                               (secret_agent_id, secret_key, entity_type, entity_id, assigned_by)
                           VALUES ($1, $2, $3, $4, $5)
                           ON CONFLICT DO NOTHING""",
                        secret_agent_id,
                        secret_key,
                        assigned_type,
                        assigned_id,
                        user_id,
                    )
    except Exception as exc:
        logger.exception("inbox.grant_secret failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    resolved_item = await svc._resolve(
        item_id,
        "approved",
        user_id,
        reason=f"Granted {secret_id} to {entity_type}:{entity_id}",
    )
    if resolved_item is None:
        return JSONResponse({"error": "not found or already resolved"}, status_code=404)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        await hub.broadcast("inbox.secret_granted", {
            "item_id": item_id,
            "secret_id": secret_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "agent_id": item.agent_id,
        })

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "inbox.grant_secret", {
        "item_id": item_id,
        "secret_id": secret_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
    })
    return JSONResponse({
        "ok": True,
        "item": resolved_item.to_dict(),
        "secret_id": secret_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
    })


# ── Agent lifecycle endpoints ────────────────────────────────
# These could live in a separate file but logically belong to
# Sprint P1's "operator controls" capability.

async def agent_pause(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/pause"""
    return await _agent_lifecycle(request, "pause")


async def agent_resume(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/resume"""
    return await _agent_lifecycle(request, "resume")


async def agent_restart(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/restart"""
    return await _agent_lifecycle(request, "restart")


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
            if method is None and action == "restart":
                method = getattr(runtime, "resume", None)
            if method is None:
                return JSONResponse({"error": f"action '{action}' not supported"}, status_code=400)
            method()  # pause(), resume(), terminate() are synchronous
        elif registry is not None:
            from amiagi.domain.agent import AgentState
            state_map = {
                "pause": AgentState.PAUSED,
                "resume": AgentState.IDLE,
                "restart": AgentState.IDLE,
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
        await hub.broadcast("agent.lifecycle", {
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
    Route("/api/inbox/batch/approve", inbox_batch_approve, methods=["POST"]),
    Route("/api/inbox/batch/reject", inbox_batch_reject, methods=["POST"]),
    Route("/api/inbox/{item_id}/approve", inbox_approve, methods=["POST"]),
    Route("/api/inbox/{item_id}/grant", inbox_grant_secret, methods=["POST"]),
    Route("/api/inbox/{item_id}/reject", inbox_reject, methods=["POST"]),
    Route("/api/inbox/{item_id}/reply", inbox_reply, methods=["POST"]),
    Route("/api/inbox/{item_id}", inbox_detail, methods=["GET"]),
    Route("/api/inbox", inbox_list, methods=["GET"]),
    # Agent lifecycle
    Route("/api/agents/{agent_id}/pause", agent_pause, methods=["POST"]),
    Route("/api/agents/{agent_id}/resume", agent_resume, methods=["POST"]),
    Route("/api/agents/{agent_id}/restart", agent_restart, methods=["POST"]),
    Route("/api/agents/{agent_id}/terminate", agent_terminate, methods=["POST"]),
]
