"""Routes: Agent cross-memory browser — GET / DELETE / search.

Exposes the in-memory :class:`CrossAgentMemory` store via REST so
the dashboard and Memory Browser can display, search and manage shared
agent findings.

Additional P3 endpoints:
    GET    /memory                — Memory Browser page
    GET    /api/memory/agents     — unique agent IDs with counts
    GET    /api/memory/shared     — cross-agent shared memories
    GET    /api/memory/search     — full-text search across all memories
    GET    /api/memory/{agent_id}/items — per-agent memories
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


async def memory_page(request: Request):
    """``GET /memory`` — render the Memory Browser page."""
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        return JSONResponse({"error": "templates unavailable"}, status_code=500)
    return templates.TemplateResponse(request, "memory.html")


async def memory_agents(request: Request) -> JSONResponse:
    """``GET /api/memory/agents`` — unique agent IDs with item counts."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"agents": []})

    all_items = mem.query(limit=10_000)
    agent_map: dict[str, int] = {}
    for item in all_items:
        agent_map[item.agent_id] = agent_map.get(item.agent_id, 0) + 1

    agents = [{"agent_id": aid, "count": cnt} for aid, cnt in sorted(agent_map.items())]
    return JSONResponse({"agents": agents})


async def memory_shared(request: Request) -> JSONResponse:
    """``GET /api/memory/shared`` — cross-agent shared memories."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"items": [], "total": 0})

    limit = int(request.query_params.get("limit", "100"))

    # Shared = tagged as 'shared' or items referenced by multiple agents
    items = mem.query(tags=["shared"], limit=limit)
    return JSONResponse({
        "items": [_item_to_dict(i) for i in items],
        "total": len(items),
    })


async def memory_search(request: Request) -> JSONResponse:
    """``GET /api/memory/search?q=...`` — full-text search across memories."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"items": [], "total": 0})

    query_text = request.query_params.get("q", "").strip().lower()
    if not query_text:
        return JSONResponse({"error": "query parameter 'q' is required"}, status_code=400)

    limit = int(request.query_params.get("limit", "50"))
    all_items = mem.query(limit=10_000)

    matched = []
    for item in all_items:
        text = (item.key_findings or "").lower()
        tags_str = " ".join(item.tags).lower() if item.tags else ""
        if query_text in text or query_text in tags_str:
            matched.append(item)
            if len(matched) >= limit:
                break

    return JSONResponse({
        "items": [_item_to_dict(i) for i in matched],
        "total": len(matched),
        "query": query_text,
    })


async def memory_per_agent(request: Request) -> JSONResponse:
    """``GET /api/memory/{agent_id}/items`` — per-agent memories."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"items": [], "total": 0})

    agent_id = request.path_params["agent_id"]
    limit = int(request.query_params.get("limit", "100"))
    items = mem.query(agent_id=agent_id, limit=limit)
    return JSONResponse({
        "items": [_item_to_dict(i) for i in items],
        "total": len(items),
    })


memory_routes: list[Route] = [
    # Page
    Route("/memory", memory_page),
    # New P3 endpoints (specific paths first)
    Route("/api/memory/agents", memory_agents, methods=["GET"]),
    Route("/api/memory/shared", memory_shared, methods=["GET"]),
    Route("/api/memory/search", memory_search, methods=["GET"]),
    Route("/api/memory/{agent_id}/items", memory_per_agent, methods=["GET"]),
    # Existing endpoints
    Route("/api/memory", list_memory, methods=["GET"]),
    Route("/api/memory", clear_memory, methods=["DELETE"]),
    Route("/api/memory/{index:int}", delete_memory_item, methods=["DELETE"]),
    Route("/api/memory/{index:int}", edit_memory_item, methods=["PUT"]),
]
