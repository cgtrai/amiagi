"""Routes: Global search API.

GET /api/search?q=...&type=...&limit=20 — full-text search
"""

from __future__ import annotations

from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.productivity.search_service import SearchResult


def _has_any_permission(request: Request, *permissions: str) -> bool:
    user = getattr(request.state, "user", None)
    granted = set(getattr(user, "permissions", []) or [])
    return any(permission in granted for permission in permissions)


def _score_match(query: str, *values: object) -> float:
    needle = query.strip().lower()
    if not needle:
        return 0.0
    best = 0.0
    for value in values:
        hay = str(value or "").strip().lower()
        if not hay:
            continue
        if hay == needle:
            best = max(best, 1.0)
        elif hay.startswith(needle):
            best = max(best, 0.92)
        elif needle in hay:
            best = max(best, 0.75)
    return best


def _merge_results(*groups: list[SearchResult], limit: int) -> list[SearchResult]:
    deduped: dict[tuple[str, str], SearchResult] = {}
    for group in groups:
        for item in group:
            key = (item.entity_type, item.entity_id)
            current = deduped.get(key)
            if current is None or item.rank > current.rank:
                deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda item: (-item.rank, item.title.lower(), item.entity_id.lower()),
    )[:limit]


async def _search_sessions(request: Request, query: str, *, limit: int) -> list[SearchResult]:
    recorder = getattr(request.app.state, "session_recorder", None)
    if recorder is None:
        return []
    sessions = await recorder.list_sessions(limit=max(limit * 5, 25))
    results: list[SearchResult] = []
    for session in sessions:
        score = _score_match(query, session.get("session_id"), session.get("agent_id"))
        if score <= 0:
            continue
        results.append(SearchResult(
            entity_type="session",
            entity_id=str(session.get("session_id") or ""),
            title=str(session.get("session_id") or "session"),
            snippet=f"Agent: {session.get('agent_id') or '—'} · Events: {session.get('event_count') or 0}",
            rank=score,
        ))
    return results[:limit]


async def _search_workflows(request: Request, query: str, *, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    definitions = getattr(request.app.state, "_workflow_definitions", {}) or {}
    for def_id, definition in definitions.items():
        score = _score_match(query, def_id, getattr(definition, "name", ""), getattr(definition, "description", ""))
        if score <= 0:
            continue
        results.append(SearchResult(
            entity_type="workflow",
            entity_id=str(def_id),
            title=str(getattr(definition, "name", None) or def_id),
            snippet=str(getattr(definition, "description", "") or "Workflow definition"),
            rank=score,
        ))

    engine = getattr(request.app.state, "workflow_engine", None)
    if engine is not None:
        for run in engine.list_runs():
            workflow_name = getattr(getattr(run, "workflow", None), "name", "workflow")
            score = _score_match(query, getattr(run, "run_id", ""), workflow_name, getattr(run, "status", ""))
            if score <= 0:
                continue
            results.append(SearchResult(
                entity_type="workflow_run",
                entity_id=str(getattr(run, "run_id", "")),
                title=f"{workflow_name} · {getattr(run, 'run_id', '')}",
                snippet=f"Status: {getattr(run, 'status', 'unknown')}",
                rank=score,
            ))
    return results[:limit]


async def _search_inbox(request: Request, query: str, *, limit: int) -> list[SearchResult]:
    inbox = getattr(request.app.state, "inbox_service", None)
    if inbox is None:
        return []
    items = await inbox.list_items(limit=max(limit * 5, 25))
    results: list[SearchResult] = []
    for item in items:
        score = _score_match(query, item.id, item.title, item.body, item.agent_id, item.status)
        if score <= 0:
            continue
        results.append(SearchResult(
            entity_type="inbox",
            entity_id=str(item.id),
            title=item.title,
            snippet=f"{item.status} · {item.body[:120] if item.body else item.item_type}",
            rank=score,
        ))
    return results[:limit]


async def _search_users_and_roles(request: Request, query: str, *, limit: int) -> list[SearchResult]:
    repo = getattr(request.app.state, "rbac_repo", None)
    if repo is None:
        return []

    results: list[SearchResult] = []
    if _has_any_permission(request, "admin.users"):
        page = await repo.list_users(page=1, per_page=max(limit * 5, 25), search=query)
        for user in page.items:
            score = _score_match(query, user.email, user.display_name)
            if score <= 0:
                continue
            results.append(SearchResult(
                entity_type="user",
                entity_id=str(user.id),
                title=user.display_name or user.email,
                snippet=user.email,
                rank=score,
            ))

    if _has_any_permission(request, "admin.roles"):
        roles = await repo.list_roles()
        for role in roles:
            score = _score_match(query, role.name, role.description)
            if score <= 0:
                continue
            results.append(SearchResult(
                entity_type="role",
                entity_id=str(role.id),
                title=role.name,
                snippet=role.description or "Role",
                rank=score,
            ))
    return results[:limit]


async def _search_vault(request: Request, query: str, *, limit: int) -> list[SearchResult]:
    if not _has_any_permission(request, "admin.settings", "admin.users"):
        return []
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return []

    rows = await pool.fetch(
        """
        SELECT agent_id, key, updated_at
        FROM dbo.vault_secrets
        ORDER BY updated_at DESC
        LIMIT $1
        """,
        max(limit * 5, 25),
    )
    results: list[SearchResult] = []
    for row in rows:
        agent_id = row.get("agent_id")
        key = row.get("key")
        score = _score_match(query, agent_id, key)
        if score <= 0:
            continue
        results.append(SearchResult(
            entity_type="vault",
            entity_id=f"{agent_id}:{key}",
            title=f"{agent_id} · {key}",
            snippet="Vault secret",
            rank=score,
            url=f"/admin/vault?agent_id={quote(str(agent_id or ''), safe='')}",
        ))
    return results[:limit]


async def api_search(request: Request) -> JSONResponse:
    svc = request.app.state.search_service
    query = request.query_params.get("q", "")
    entity_type = request.query_params.get("type")
    limit = min(int(request.query_params.get("limit", "20")), 100)
    offset = int(request.query_params.get("offset", "0"))

    indexed_results = await svc.search(query, entity_type=entity_type, limit=limit, offset=offset)
    extra_results: list[SearchResult] = []
    if entity_type in (None, "session"):
        extra_results.extend(await _search_sessions(request, query, limit=limit))
    if entity_type in (None, "workflow", "workflow_run"):
        extra_results.extend(await _search_workflows(request, query, limit=limit))
    if entity_type in (None, "inbox"):
        extra_results.extend(await _search_inbox(request, query, limit=limit))
    if entity_type in (None, "user", "role"):
        extra_results.extend(await _search_users_and_roles(request, query, limit=limit))
    if entity_type in (None, "vault"):
        extra_results.extend(await _search_vault(request, query, limit=limit))

    results = _merge_results(indexed_results, extra_results, limit=limit)
    return JSONResponse([r.to_dict() for r in results])


async def search_suggestions(request: Request) -> JSONResponse:
    """GET /api/search/suggestions?q=partial — autocomplete + recent queries."""
    q = request.query_params.get("q", "")
    svc = request.app.state.search_service
    recent = list(svc.get_recent_queries(limit=5)) if hasattr(svc, "get_recent_queries") else []
    queries = list(svc.get_frequent_queries(q, limit=5)) if hasattr(svc, "get_frequent_queries") else []
    suggestions: list[dict] = []
    if len(q) >= 2:
        suggestions = [r.to_dict() for r in await svc.search(q, limit=5, remember=False)]
    return JSONResponse({"suggestions": suggestions, "recent": recent, "queries": queries})


search_routes = [
    Route("/api/search/suggestions", search_suggestions, methods=["GET"]),
    Route("/api/search", api_search, methods=["GET"]),
]
