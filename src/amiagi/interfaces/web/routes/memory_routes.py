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

from amiagi.application.cross_agent_memory import MemoryItem
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


# ── helpers ──────────────────────────────────────────────────────

def _item_to_dict(item: object) -> dict:
    """Serialise a :class:`MemoryItem` to a JSON-safe dict."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}  # type: ignore[attr-defined]
    item_type = str(metadata.get("type") or (item.tags[0] if item.tags else "note"))  # type: ignore[attr-defined]
    is_shared = bool(metadata.get("shared")) or "shared" in (item.tags or [])  # type: ignore[attr-defined]
    return {
        "agent_id": item.agent_id,  # type: ignore[attr-defined]
        "task_id": item.task_id,  # type: ignore[attr-defined]
        "key_findings": item.key_findings,  # type: ignore[attr-defined]
        "timestamp": item.timestamp,  # type: ignore[attr-defined]
        "tags": item.tags,  # type: ignore[attr-defined]
        "metadata": metadata,
        "item_type": item_type,
        "memory_scope": "shared" if is_shared else "local",
        "links": {
            "agent": f"/agents/{item.agent_id}",  # type: ignore[attr-defined]
            "task": f"/tasks?task_id={item.task_id}" if getattr(item, "task_id", "") else "",  # type: ignore[attr-defined]
        },
    }


def _matching_items(mem, *, agent_id: str | None = None, task_id: str | None = None, tags: list[str] | None = None) -> list[tuple[int, MemoryItem]]:
    """Return matching items with stable backing-store indexes, newest first."""
    with mem._lock:
        indexed_items = list(enumerate(mem._items))

    if agent_id is not None:
        indexed_items = [(idx, item) for idx, item in indexed_items if item.agent_id == agent_id]
    if task_id is not None:
        indexed_items = [(idx, item) for idx, item in indexed_items if item.task_id == task_id]
    if tags:
        tag_set = set(tags)
        indexed_items = [(idx, item) for idx, item in indexed_items if tag_set & set(item.tags)]

    indexed_items.sort(key=lambda pair: pair[1].timestamp, reverse=True)
    return indexed_items


def _serialize_items(indexed_items: list[tuple[int, MemoryItem]], *, limit: int) -> list[dict]:
    """Serialise a subset of indexed memory items."""
    return [
        {"index": index, **_item_to_dict(item)}
        for index, item in indexed_items[:limit]
    ]


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
    indexed_items = _matching_items(mem, agent_id=agent_id, task_id=task_id, tags=tags_raw or None)
    return JSONResponse({
        "items": _serialize_items(indexed_items, limit=limit),
        "total": len(indexed_items),
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

    try:
        body: dict = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

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
        if "task_id" in body:
            item.task_id = str(body["task_id"] or "")

    return JSONResponse({"ok": True, "updated": {"index": idx, **_item_to_dict(item)}})


async def create_memory_item(request: Request) -> JSONResponse:
    """``POST /api/memory`` — create a new memory item."""
    mem = getattr(request.app.state, "cross_memory", None)
    if mem is None:
        return JSONResponse({"error": "memory unavailable"}, status_code=503)

    try:
        body: dict = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent_id = str(body.get("agent_id", "")).strip()
    key_findings = str(body.get("key_findings", "")).strip()
    if not agent_id:
        return JSONResponse({"error": "agent_id required"}, status_code=400)
    if not key_findings:
        return JSONResponse({"error": "key_findings required"}, status_code=400)

    item = MemoryItem(
        agent_id=agent_id,
        task_id=str(body.get("task_id", "") or ""),
        key_findings=key_findings,
        tags=list(body.get("tags", []) or []),
        metadata=body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {},
    )
    mem.store(item)

    with mem._lock:
        item_index = len(mem._items) - 1

    return JSONResponse({"ok": True, "item": {"index": item_index, **_item_to_dict(item)}}, status_code=201)


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
    indexed_items = _matching_items(mem, tags=["shared"])
    return JSONResponse({
        "items": _serialize_items(indexed_items, limit=limit),
        "total": min(len(indexed_items), limit),
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

    matched: list[tuple[int, MemoryItem]] = []
    indexed_items = _matching_items(mem)
    for index, item in indexed_items:
        text = (item.key_findings or "").lower()
        tags_str = " ".join(item.tags).lower() if item.tags else ""
        if query_text in text or query_text in tags_str:
            matched.append((index, item))
            if len(matched) >= limit:
                break

    return JSONResponse({
        "items": _serialize_items(matched, limit=limit),
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
    indexed_items = _matching_items(mem, agent_id=agent_id)
    return JSONResponse({
        "items": _serialize_items(indexed_items, limit=limit),
        "total": min(len(indexed_items), limit),
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
    Route("/api/memory", create_memory_item, methods=["POST"]),
    Route("/api/memory", clear_memory, methods=["DELETE"]),
    Route("/api/memory/{index:int}", delete_memory_item, methods=["DELETE"]),
    Route("/api/memory/{index:int}", edit_memory_item, methods=["PUT"]),
]
