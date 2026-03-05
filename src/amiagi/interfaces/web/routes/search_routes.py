"""Routes: Global search API.

GET /api/search?q=...&type=...&limit=20 — full-text search
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def api_search(request: Request) -> JSONResponse:
    svc = request.app.state.search_service
    query = request.query_params.get("q", "")
    entity_type = request.query_params.get("type")
    limit = min(int(request.query_params.get("limit", "20")), 100)
    offset = int(request.query_params.get("offset", "0"))

    results = await svc.search(query, entity_type=entity_type, limit=limit, offset=offset)
    return JSONResponse([r.to_dict() for r in results])


search_routes = [
    Route("/api/search", api_search, methods=["GET"]),
]
