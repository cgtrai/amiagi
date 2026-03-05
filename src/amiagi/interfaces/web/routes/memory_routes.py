"""Routes: Agent cross-memory browser — GET / DELETE.

Exposes the in-memory :class:`CrossAgentMemory` store via REST so
the dashboard can display and manage shared agent findings.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


# ── helpers ──────────────────────────────────────────────────────

def _item_to_dict(item: object) -> dict:
    """Serialise a :class:`MemoryItem` to a JSON-safe dict."""
    return {
        "agent_id": item.agent_id,  # type: ignore[attr-defined]
        "task_id": item.task_id,  # type: ignore[attr-defined]
        "key_findings": item.key_findings,  # type: ignore[attr-defined]
        "timestamp": item.timestamp,  # type: ignore[attr-defined]
        "tags": item.tags,  # type: ignore[attr-defined]
        "metadata": item.metadata,  # type: ignore[attr-defined]
    }


# ── endpoints ────────────────────────────────────────────────────

async def list_memory(request: Request) -> JSONResponse:
    """``GET /api/memory`` — return all cross-agent memory items.

    Query params:
      - agent_id  — filter by agent
      - task_id   — filter by task
      - tag       — filter by tag (repeatable)
      - limit     — max items (default 100)
    """
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"items": [], "total": 0})

    agent_id = request.query_params.get("agent_id")
    task_id = request.query_params.get("task_id")
    tags_raw = request.query_params.getlist("tag")
    limit = int(request.query_params.get("limit", "100"))

    items = mem.query(
        agent_id=agent_id,
        task_id=task_id,
        tags=tags_raw or None,
        limit=limit,
    )
    return JSONResponse({
        "items": [_item_to_dict(i) for i in items],
        "total": mem.count(),
    })


async def delete_memory_item(request: Request) -> JSONResponse:
    """``DELETE /api/memory/{index}`` — remove a single item by 0-based index."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"error": "memory unavailable"}, status_code=503)

    try:
        idx = int(request.path_params["index"])
    except (KeyError, ValueError):
        return JSONResponse({"error": "invalid index"}, status_code=400)

    with mem._lock:
        if idx < 0 or idx >= len(mem._items):
            return JSONResponse({"error": "index out of range"}, status_code=404)
        mem._items.pop(idx)

    return JSONResponse({"ok": True})


async def clear_memory(request: Request) -> JSONResponse:
    """``DELETE /api/memory`` — clear all memory items."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"error": "memory unavailable"}, status_code=503)

    mem.clear()
    return JSONResponse({"ok": True, "cleared": True})


async def edit_memory_item(request: Request) -> JSONResponse:
    """``PUT /api/memory/{index}`` — edit a memory item by 0-based index.

    Accepts JSON body with optional fields: ``key_findings``, ``tags``,
    ``metadata``.  Only provided fields are updated.
    """
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"error": "memory unavailable"}, status_code=503)

    try:
        idx = int(request.path_params["index"])
    except (KeyError, ValueError):
        return JSONResponse({"error": "invalid index"}, status_code=400)

    body: dict = await request.json()

    with mem._lock:
        if idx < 0 or idx >= len(mem._items):
            return JSONResponse({"error": "index out of range"}, status_code=404)
        item = mem._items[idx]
        if "key_findings" in body:
            item.key_findings = body["key_findings"]
        if "tags" in body:
            item.tags = body["tags"]
        if "metadata" in body:
            item.metadata = body["metadata"]

    return JSONResponse({"ok": True, "updated": _item_to_dict(item)})


# ── route table ──────────────────────────────────────────────────

memory_routes: list[Route] = [
    Route("/api/memory", list_memory, methods=["GET"]),
    Route("/api/memory", clear_memory, methods=["DELETE"]),
    Route("/api/memory/{index:int}", delete_memory_item, methods=["DELETE"]),
    Route("/api/memory/{index:int}", edit_memory_item, methods=["PUT"]),
]
