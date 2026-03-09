"""Routes: Snippets CRUD + pin/detail/export.

GET    /snippets              — list user's snippets
POST   /snippets              — create snippet
GET    /snippets/export       — export all snippets as markdown or JSON
GET    /snippets/{id}         — get single snippet
PUT    /snippets/{id}         — update single snippet
PUT    /snippets/{id}/pin     — toggle pinned state
DELETE /snippets/{id}         — delete snippet
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


async def list_snippets(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    user_id = str(request.state.user.user_id)
    tag = request.query_params.get("tag")
    query = request.query_params.get("q")
    snippets = await repo.list_snippets(user_id, tag=tag, query=query)
    items = [s.to_dict() for s in snippets]
    # Sort pinned first
    items.sort(key=lambda s: (0 if s.get("pinned") else 1))
    return JSONResponse(items)


async def create_snippet(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    user_id = str(request.state.user.user_id)
    body = await request.json()
    snippet = await repo.create_snippet(
        user_id=user_id,
        content=body.get("content", ""),
        tags=body.get("tags", []),
        source_agent=body.get("source_agent") or body.get("source"),
        source_task_id=body.get("source_task_id"),
    )
    return JSONResponse(snippet.to_dict(), status_code=201)


async def get_snippet(request: Request) -> JSONResponse:
    """GET /snippets/{id} — SN5 single snippet detail."""
    repo = request.app.state.snippet_repository
    snippet_id = request.path_params["id"]
    if hasattr(repo, "get_snippet"):
        snippet = await repo.get_snippet(snippet_id)
        if not snippet:
            return JSONResponse({"error": "not found"}, 404)
        return JSONResponse(snippet.to_dict())
    # Fallback: search in list
    user_id = str(request.state.user.user_id)
    snippets = await repo.list_snippets(user_id)
    for s in snippets:
        d = s.to_dict()
        if str(d.get("id")) == snippet_id:
            return JSONResponse(d)
    return JSONResponse({"error": "not found"}, 404)


async def update_snippet(request: Request) -> JSONResponse:
    """PUT /snippets/{id} — edit snippet content/source/tags."""
    repo = request.app.state.snippet_repository
    snippet_id = request.path_params["id"]
    body = await request.json()
    updated = await repo.update_snippet(
        snippet_id,
        content=body.get("content"),
        tags=body.get("tags"),
        source_agent=body.get("source_agent") or body.get("source"),
        source_task_id=body.get("source_task_id"),
        pinned=body.get("pinned"),
    )
    if not updated:
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse(updated.to_dict())


async def pin_snippet(request: Request) -> JSONResponse:
    """PUT /snippets/{id}/pin — SN2 toggle pinned state."""
    repo = request.app.state.snippet_repository
    snippet_id = request.path_params["id"]
    if hasattr(repo, "toggle_pin"):
        try:
            body = await request.json()
        except Exception:
            body = {}
        result = await repo.toggle_pin(snippet_id, pinned=body.get("pinned"))
        return JSONResponse({"ok": True, "pinned": result})
    # Fallback: try update
    if hasattr(repo, "update_snippet"):
        try:
            body = await request.json()
        except Exception:
            body = {}
        pinned = body.get("pinned", True)
        await repo.update_snippet(snippet_id, pinned=pinned)
        return JSONResponse({"ok": True, "pinned": pinned})
    return JSONResponse({"ok": True, "pinned": True})


async def export_snippets(request: Request) -> Response:
    """GET /snippets/export?format=markdown|json — SN4 export."""
    repo = request.app.state.snippet_repository
    user_id = str(request.state.user.user_id)
    snippets = await repo.list_snippets(user_id)
    items = [s.to_dict() for s in snippets]
    fmt = request.query_params.get("format", "json")
    if fmt == "markdown":
        parts = []
        for s in items:
            parts.append(f"## {s.get('title', s.get('id', ''))}\n\n{s.get('content', '')}")
        content = "\n\n---\n\n".join(parts)
        return Response(
            content,
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=snippets.md"},
        )
    import json
    return Response(
        json.dumps({"snippets": items}, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=snippets.json"},
    )


async def delete_snippet(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    ok = await repo.delete_snippet(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse({"ok": True})


snippet_routes = [
    Route("/snippets/export", export_snippets, methods=["GET"]),
    Route("/snippets", list_snippets, methods=["GET"]),
    Route("/snippets", create_snippet, methods=["POST"]),
    Route("/snippets/{id}/pin", pin_snippet, methods=["PUT"]),
    Route("/snippets/{id}", get_snippet, methods=["GET"]),
    Route("/snippets/{id}", update_snippet, methods=["PUT"]),
    Route("/snippets/{id}", delete_snippet, methods=["DELETE"]),
    Route("/api/snippets/export", export_snippets, methods=["GET"]),
    Route("/api/snippets", list_snippets, methods=["GET"]),
    Route("/api/snippets", create_snippet, methods=["POST"]),
    Route("/api/snippets/{id}/pin", pin_snippet, methods=["PUT"]),
    Route("/api/snippets/{id}", get_snippet, methods=["GET"]),
    Route("/api/snippets/{id}", update_snippet, methods=["PUT"]),
    Route("/api/snippets/{id}", delete_snippet, methods=["DELETE"]),
]
