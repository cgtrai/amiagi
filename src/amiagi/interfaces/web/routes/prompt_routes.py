"""Routes: Shared Prompts Library CRUD.

GET  /prompts              — list prompts (user's own + public)
POST /prompts              — create prompt
GET  /prompts/{id}         — get prompt
PUT  /prompts/{id}         — update prompt
DELETE /prompts/{id}       — delete prompt
POST /prompts/{id}/use     — increment use_count and return rendered template
"""

from __future__ import annotations

import json
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_prompts(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    user_id = str(request.state.user.user_id)
    tag = request.query_params.get("tag")
    query = request.query_params.get("q")
    prompts = await repo.list_prompts(user_id=user_id, tag=tag, query=query)
    items = []
    for prompt in prompts:
        item = prompt.to_dict()
        if hasattr(repo, "get_prompt_stats"):
            stats = await repo.get_prompt_stats(prompt.id)
            item["usage_count"] = stats.get("total_uses", item.get("use_count", 0))
            item["agent_count"] = stats.get("agent_count", 0)
        else:
            item["usage_count"] = item.get("use_count", 0)
            item["agent_count"] = 0
        items.append(item)
    return JSONResponse(items)


async def create_prompt(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    user_id = str(request.state.user.user_id)
    body = await request.json()
    prompt = await repo.create_prompt(
        user_id=user_id,
        title=body.get("title", ""),
        template=body.get("template", ""),
        tags=body.get("tags", []),
        is_public=body.get("is_public", False),
    )
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "prompt.create", {"prompt_id": str(prompt.id), "title": prompt.title})
    return JSONResponse(prompt.to_dict(), status_code=201)


async def get_prompt(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    prompt = await repo.get_prompt(request.path_params["id"])
    if not prompt:
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse(prompt.to_dict())


async def update_prompt(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    body = await request.json()
    prompt = await repo.update_prompt(request.path_params["id"], **body)
    if not prompt:
        return JSONResponse({"error": "not found"}, 404)
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "prompt.update", {"prompt_id": request.path_params["id"]})
    return JSONResponse(prompt.to_dict())


async def delete_prompt(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    ok = await repo.delete_prompt(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not found"}, 404)
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "prompt.delete", {"prompt_id": request.path_params["id"]})
    return JSONResponse({"ok": True})


async def use_prompt(request: Request) -> JSONResponse:
    repo = request.app.state.prompt_repository
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    prompt = await repo.get_prompt(request.path_params["id"])
    if not prompt:
        return JSONResponse({"error": "not found"}, 404)
    rendered = prompt.render(body.get("values", {}))
    agent_id = body.get("agent_id")
    if hasattr(repo, "record_prompt_use"):
        await repo.record_prompt_use(request.path_params["id"], agent_id=agent_id)
    else:
        await repo.increment_use_count(request.path_params["id"])
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "prompt.use", {"prompt_id": request.path_params["id"], "agent_id": agent_id})
    return JSONResponse({"rendered": rendered, "parameters": prompt.parameters, "agent_id": agent_id})


async def clone_prompt(request: Request) -> JSONResponse:
    """``POST /prompts/{id}/clone`` — duplicate an existing prompt for the current user."""
    repo = request.app.state.prompt_repository
    user_id = str(request.state.user.user_id)
    source = await repo.get_prompt(request.path_params["id"])
    if not source:
        return JSONResponse({"error": "not found"}, 404)
    cloned = await repo.create_prompt(
        user_id=user_id,
        title=f"{source.title} (copy)",
        template=source.template,
        tags=list(source.tags),
        is_public=False,
    )
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "prompt.clone", {"source_id": request.path_params["id"], "cloned_id": str(cloned.id)})
    return JSONResponse(cloned.to_dict(), status_code=201)


async def prompt_stats(request: Request) -> JSONResponse:
    """GET /prompts/{id}/stats — usage statistics for a prompt."""
    repo = request.app.state.prompt_repository
    prompt = await repo.get_prompt(request.path_params["id"])
    if not prompt:
        return JSONResponse({"error": "not found"}, 404)
    stats = {
        "total_uses": getattr(prompt, "use_count", 0) or 0,
        "agent_count": 0,
        "avg_rating": None,
    }
    if hasattr(repo, "get_prompt_stats"):
        try:
            extra = await repo.get_prompt_stats(request.path_params["id"])
            if extra:
                stats.update(extra)
        except Exception:
            pass
    return JSONResponse(stats)


prompt_routes = [
    Route("/prompts", list_prompts, methods=["GET"]),
    Route("/prompts", create_prompt, methods=["POST"]),
    Route("/prompts/{id}/stats", prompt_stats, methods=["GET"]),
    Route("/prompts/{id}/clone", clone_prompt, methods=["POST"]),
    Route("/prompts/{id}/use", use_prompt, methods=["POST"]),
    Route("/prompts/{id}", get_prompt, methods=["GET"]),
    Route("/prompts/{id}", update_prompt, methods=["PUT"]),
    Route("/prompts/{id}", delete_prompt, methods=["DELETE"]),
    Route("/api/prompts", list_prompts, methods=["GET"]),
    Route("/api/prompts", create_prompt, methods=["POST"]),
    Route("/api/prompts/{id}/stats", prompt_stats, methods=["GET"]),
    Route("/api/prompts/{id}/clone", clone_prompt, methods=["POST"]),
    Route("/api/prompts/{id}/use", use_prompt, methods=["POST"]),
    Route("/api/prompts/{id}", get_prompt, methods=["GET"]),
    Route("/api/prompts/{id}", update_prompt, methods=["PUT"]),
    Route("/api/prompts/{id}", delete_prompt, methods=["DELETE"]),
]
