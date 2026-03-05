"""Routes: Snippets CRUD.

GET    /snippets           — list user's snippets
POST   /snippets           — create snippet
DELETE /snippets/{id}      — delete snippet
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_snippets(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    user_id = str(request.state.user.get("sub", ""))
    tag = request.query_params.get("tag")
    snippets = await repo.list_snippets(user_id, tag=tag)
    return JSONResponse([s.to_dict() for s in snippets])


async def create_snippet(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    user_id = str(request.state.user.get("sub", ""))
    body = await request.json()
    snippet = await repo.create_snippet(
        user_id=user_id,
        content=body.get("content", ""),
        tags=body.get("tags", []),
        source_agent=body.get("source_agent"),
        source_task_id=body.get("source_task_id"),
    )
    return JSONResponse(snippet.to_dict(), status_code=201)


async def delete_snippet(request: Request) -> JSONResponse:
    repo = request.app.state.snippet_repository
    ok = await repo.delete_snippet(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse({"ok": True})


snippet_routes = [
    Route("/snippets", list_snippets, methods=["GET"]),
    Route("/snippets", create_snippet, methods=["POST"]),
    Route("/snippets/{id}", delete_snippet, methods=["DELETE"]),
]
