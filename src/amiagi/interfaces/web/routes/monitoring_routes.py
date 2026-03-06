"""Routes: Performance, notifications, session replay, API keys, webhooks.

Faza 13 monitoring & integration routes.
"""

from __future__ import annotations

import json
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


# ── Performance ─────────────────────────────────────────────────

async def api_performance(request: Request) -> JSONResponse:
    tracker = request.app.state.performance_tracker
    agent = request.query_params.get("agent")
    model = request.query_params.get("model")
    since = request.query_params.get("from")
    until = request.query_params.get("to")
    records = await tracker.query(agent_role=agent, model=model, since=since, until=until)
    return JSONResponse([r.to_dict() for r in records])


async def api_performance_summary(request: Request) -> JSONResponse:
    tracker = request.app.state.performance_tracker
    agent = request.query_params.get("agent")
    model = request.query_params.get("model")
    summary = await tracker.summary(agent_role=agent, model=model)
    return JSONResponse(summary)


# ── Notifications ───────────────────────────────────────────────

async def api_notifications(request: Request) -> JSONResponse:
    svc = request.app.state.notification_service
    user_id = str(request.state.user.user_id)
    unread = request.query_params.get("unread") == "true"
    notifs = await svc.list_for_user(user_id, unread_only=unread)
    count = await svc.unread_count(user_id)
    return JSONResponse({"unread_count": count, "notifications": [n.to_dict() for n in notifs]})


async def api_notification_read(request: Request) -> JSONResponse:
    svc = request.app.state.notification_service
    ok = await svc.mark_read(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def api_notifications_read_all(request: Request) -> JSONResponse:
    svc = request.app.state.notification_service
    user_id = str(request.state.user.user_id)
    count = await svc.mark_all_read(user_id)
    return JSONResponse({"marked": count})


# ── Session Replay ──────────────────────────────────────────────

async def api_sessions(request: Request) -> JSONResponse:
    recorder = request.app.state.session_recorder
    sessions = await recorder.list_sessions()
    return JSONResponse(sessions)


async def api_session_events(request: Request) -> JSONResponse:
    recorder = request.app.state.session_recorder
    sid = request.path_params["session_id"]
    events = await recorder.get_session_events(sid)
    return JSONResponse([e.to_dict() for e in events])


async def api_session_replay(request: Request) -> JSONResponse:
    """``GET /api/sessions/{session_id}/replay`` — events formatted for timeline playback."""
    recorder = request.app.state.session_recorder
    sid = request.path_params["session_id"]
    events = await recorder.get_session_events(sid, limit=2000)
    replay = {
        "session_id": sid,
        "event_count": len(events),
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "agent_id": e.agent_id,
                "payload": e.payload,
                "timestamp": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }
    return JSONResponse(replay)


# ── API Keys ────────────────────────────────────────────────────

async def api_keys_list(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    user_id = str(request.state.user.user_id)
    keys = await mgr.list_keys(user_id)
    return JSONResponse([k.to_dict() for k in keys])


async def api_keys_create(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    user_id = str(request.state.user.user_id)
    body = await request.json()
    raw_key, record = await mgr.create_key(
        user_id, body.get("name", "Unnamed"),
        scopes=body.get("scopes", []),
    )
    result = record.to_dict()
    result["raw_key"] = raw_key  # Only shown once
    return JSONResponse(result, status_code=201)


async def api_keys_revoke(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    ok = await mgr.revoke_key(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def api_keys_delete(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    ok = await mgr.delete_key(request.path_params["id"])
    return JSONResponse({"ok": ok})


# ── Webhooks ────────────────────────────────────────────────────

async def webhooks_list(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    user_id = str(request.state.user.user_id)
    hooks = await mgr.list_webhooks(user_id)
    return JSONResponse([h.to_dict() for h in hooks])


async def webhooks_create(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    user_id = str(request.state.user.user_id)
    body = await request.json()
    hook = await mgr.create_webhook(
        user_id, body.get("url", ""), body.get("events", []),
        secret=body.get("secret"),
    )
    return JSONResponse(hook.to_dict(), status_code=201)


async def webhooks_delete(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    ok = await mgr.delete_webhook(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def webhooks_test(request: Request) -> JSONResponse:
    """Send a test payload to a specific webhook."""
    mgr = request.app.state.webhook_manager
    hooks = await mgr.list_webhooks(str(request.state.user.user_id))
    target = [h for h in hooks if h.id == request.path_params["id"]]
    if not target:
        return JSONResponse({"error": "not found"}, 404)
    results = await mgr.dispatch("webhook.test", {"message": "Test from amiagi"})
    return JSONResponse({"results": results})


monitoring_routes = [
    # Performance
    Route("/api/performance", api_performance, methods=["GET"]),
    Route("/api/performance/summary", api_performance_summary, methods=["GET"]),
    # Notifications
    Route("/api/notifications", api_notifications, methods=["GET"]),
    Route("/api/notifications/read-all", api_notifications_read_all, methods=["PUT"]),
    Route("/api/notifications/{id}/read", api_notification_read, methods=["PUT"]),
    # Session Replay
    Route("/api/sessions", api_sessions, methods=["GET"]),
    Route("/api/sessions/{session_id}/events", api_session_events, methods=["GET"]),
    Route("/api/sessions/{session_id}/replay", api_session_replay, methods=["GET"]),
    # API Keys
    Route("/settings/api-keys", api_keys_list, methods=["GET"]),
    Route("/settings/api-keys", api_keys_create, methods=["POST"]),
    Route("/settings/api-keys/{id}/revoke", api_keys_revoke, methods=["PUT"]),
    Route("/settings/api-keys/{id}", api_keys_delete, methods=["DELETE"]),
    # Webhooks
    Route("/settings/webhooks", webhooks_list, methods=["GET"]),
    Route("/settings/webhooks", webhooks_create, methods=["POST"]),
    Route("/settings/webhooks/{id}", webhooks_delete, methods=["DELETE"]),
    Route("/settings/webhooks/{id}/test", webhooks_test, methods=["POST"]),
]
