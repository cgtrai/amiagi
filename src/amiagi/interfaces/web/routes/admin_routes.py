"""Admin panel routes — user and role management."""

from __future__ import annotations

import logging
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from amiagi.interfaces.web.rbac.middleware import require_permission
from amiagi.interfaces.web.audit.log_helpers import log_action

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------

@require_permission("admin.users")
async def admin_list_users(request: Request) -> Response:
    """GET /admin/users — paginated user list."""
    repo = request.app.state.rbac_repo
    page = int(request.query_params.get("page", "1"))
    per_page = int(request.query_params.get("per_page", "20"))
    search = request.query_params.get("q")

    result = await repo.list_users(page=page, per_page=per_page, search=search)

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/users.html",
            {
                "users": result.items,
                "page": result,
                "search": search or "",
                "user": request.state.user,
            },
        )

    return JSONResponse({
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "is_active": u.is_active,
                "is_blocked": u.is_blocked,
                "roles": [r.name for r in u.roles],
            }
            for u in result.items
        ],
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    })


@require_permission("admin.users")
async def admin_user_detail(request: Request) -> Response:
    """GET /admin/users/{id} — single user details."""
    repo = request.app.state.rbac_repo
    user_id = UUID(request.path_params["id"])
    user = await repo.get_user_by_id(user_id)
    if user is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    roles = await repo.list_roles()

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/user_detail.html",
            {"target_user": user, "all_roles": roles, "user": request.state.user},
        )

    return JSONResponse({
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "is_blocked": user.is_blocked,
        "roles": [{"id": str(r.id), "name": r.name} for r in user.roles],
        "permissions": user.permissions,
    })


@require_permission("admin.users")
async def admin_update_user_roles(request: Request) -> Response:
    """POST /admin/users/{id}/roles — update user roles."""
    repo = request.app.state.rbac_repo
    user_id = UUID(request.path_params["id"])
    body = await request.json()
    role_ids = body.get("role_ids", [])

    # Clear existing roles and assign new ones
    user = await repo.get_user_by_id(user_id)
    if user is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # Remove current roles
    for role in user.roles:
        await repo.remove_role(user_id, role.id)

    # Assign new roles
    for rid in role_ids:
        await repo.assign_role(user_id, UUID(rid))

    await log_action(request, "admin.update_roles", {"target_user_id": str(user_id), "role_ids": role_ids})
    return JSONResponse({"status": "ok"})


@require_permission("admin.users")
async def admin_block_user(request: Request) -> Response:
    """POST /admin/users/{id}/block — block a user and revoke sessions."""
    repo = request.app.state.rbac_repo
    session_manager = request.app.state.session_manager
    user_id = UUID(request.path_params["id"])

    ok = await repo.block_user(user_id)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # Revoke all sessions
    await session_manager.revoke_all_user_sessions(user_id)

    await log_action(request, "admin.block_user", {"target_user_id": str(user_id)})
    return JSONResponse({"status": "blocked"})


@require_permission("admin.users")
async def admin_activate_user(request: Request) -> Response:
    """POST /admin/users/{id}/activate — re-activate a user."""
    repo = request.app.state.rbac_repo
    user_id = UUID(request.path_params["id"])

    ok = await repo.activate_user(user_id)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await log_action(request, "admin.activate_user", {"target_user_id": str(user_id)})
    return JSONResponse({"status": "activated"})


# ------------------------------------------------------------------
# Roles
# ------------------------------------------------------------------

@require_permission("admin.roles")
async def admin_list_roles(request: Request) -> Response:
    """GET /admin/roles — all roles with permissions."""
    repo = request.app.state.rbac_repo
    roles = await repo.list_roles()

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/roles.html",
            {"roles": roles, "user": request.state.user},
        )

    return JSONResponse({
        "roles": [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "is_system": r.is_system,
                "permissions": [p.codename for p in r.permissions],
            }
            for r in roles
        ],
    })


@require_permission("admin.roles")
async def admin_create_role(request: Request) -> Response:
    """POST /admin/roles — create a new role."""
    repo = request.app.state.rbac_repo
    body = await request.json()
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    permission_ids = body.get("permission_ids", [])

    if not name:
        return JSONResponse({"error": "name_required"}, status_code=400)

    role = await repo.create_role(name, description, [UUID(p) for p in permission_ids])
    await log_action(request, "admin.create_role", {"role_id": str(role.id), "name": role.name})
    return JSONResponse(
        {"id": str(role.id), "name": role.name, "description": role.description},
        status_code=201,
    )


@require_permission("admin.roles")
async def admin_update_role(request: Request) -> Response:
    """PUT /admin/roles/{id} — update role name, description, permissions."""
    repo = request.app.state.rbac_repo
    role_id = UUID(request.path_params["id"])
    body = await request.json()

    perm_ids = None
    if "permission_ids" in body:
        perm_ids = [UUID(p) for p in body["permission_ids"]]

    role = await repo.update_role(
        role_id,
        name=body.get("name"),
        description=body.get("description"),
        permission_ids=perm_ids,
    )
    if role is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await log_action(request, "admin.update_role", {"role_id": str(role.id), "name": role.name})
    return JSONResponse({"id": str(role.id), "name": role.name})


@require_permission("admin.roles")
async def admin_delete_role(request: Request) -> Response:
    """DELETE /admin/roles/{id} — delete non-system role."""
    repo = request.app.state.rbac_repo
    role_id = UUID(request.path_params["id"])

    ok = await repo.delete_role(role_id)
    if not ok:
        return JSONResponse(
            {"error": "cannot_delete", "detail": "System roles cannot be deleted."},
            status_code=400,
        )

    await log_action(request, "admin.delete_role", {"role_id": str(role_id)})
    return JSONResponse({"status": "deleted"})


# ------------------------------------------------------------------
# Permissions matrix
# ------------------------------------------------------------------

@require_permission("admin.roles")
async def admin_list_permissions(request: Request) -> Response:
    """GET /admin/permissions — full permission catalogue."""
    # Browser navigation → render HTML template
    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/permissions.html",
            {"user": request.state.user},
        )

    # JS fetch / API → return JSON
    repo = request.app.state.rbac_repo
    perms = await repo.list_permissions()
    return JSONResponse({
        "permissions": [
            {"id": str(p.id), "codename": p.codename, "description": p.description, "category": p.category}
            for p in perms
        ],
    })


# ------------------------------------------------------------------
# Audit log
# ------------------------------------------------------------------

@require_permission("admin.audit")
async def admin_audit_log(request: Request) -> Response:
    """GET /admin/audit — user activity log with optional filters.

    Query params: user (user_id), action, since, until, page, per_page.
    """
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        return await _audit_via_logger(request, activity_logger)
    return await _audit_fallback(request)


async def _audit_via_logger(request: Request, activity_logger) -> Response:
    """Audit endpoint using WebActivityLogger (with filters)."""
    from datetime import datetime, timezone

    params = request.query_params
    page = int(params.get("page", "1"))
    per_page = int(params.get("per_page", "50"))

    filter_kw: dict = {}
    if params.get("user"):
        filter_kw["user_id"] = params["user"]
    if params.get("action"):
        filter_kw["action"] = params["action"]
    if params.get("since"):
        filter_kw["since"] = datetime.fromisoformat(params["since"]).replace(tzinfo=timezone.utc)
    if params.get("until"):
        filter_kw["until"] = datetime.fromisoformat(params["until"]).replace(tzinfo=timezone.utc)

    rows = await activity_logger.query(**filter_kw, limit=per_page, offset=(page - 1) * per_page)
    total = await activity_logger.count(
        user_id=filter_kw.get("user_id"),
        action=filter_kw.get("action"),
    )

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/audit.html",
            {"logs": rows, "total": total, "page": page, "per_page": per_page, "user": request.state.user},
        )

    return JSONResponse({
        "items": [
            {
                "id": r["id"],
                "user_id": str(r["user_id"]) if r.get("user_id") else None,
                "session_id": str(r["session_id"]) if r.get("session_id") else None,
                "action": r["action"],
                "detail": r.get("detail"),
                "ip_address": str(r["ip_address"]) if r.get("ip_address") else None,
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
    })


async def _audit_fallback(request: Request) -> Response:
    """Fallback audit endpoint using raw SQL (no WebActivityLogger)."""
    pool = request.app.state.db_pool
    page = int(request.query_params.get("page", "1"))
    per_page = int(request.query_params.get("per_page", "50"))
    offset = (page - 1) * per_page

    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM user_activity_log")
        rows = await conn.fetch(
            """
            SELECT al.id, al.user_id, u.email, al.action, al.detail, al.ip_address, al.created_at
            FROM user_activity_log al
            LEFT JOIN users u ON u.id = al.user_id
            ORDER BY al.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            per_page,
            offset,
        )

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/audit.html",
            {"logs": rows, "total": total, "page": page, "per_page": per_page, "user": request.state.user},
        )

    return JSONResponse({
        "items": [
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]) if r["user_id"] else None,
                "email": r["email"],
                "action": r["action"],
                "detail": r["detail"],
                "ip_address": r["ip_address"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
    })


@require_permission("admin.audit")
async def admin_audit_export(request: Request) -> Response:
    """GET /admin/audit/export — CSV or JSON export of audit log.

    Query params:
        format: ``csv`` (default) or ``json``
        user, action, since, until — optional filters.
    """
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is None:
        return JSONResponse({"error": "activity_logger_not_available"}, status_code=503)

    from datetime import datetime, timezone

    params = request.query_params
    filter_kw: dict = {}
    if params.get("user"):
        filter_kw["user_id"] = params["user"]
    if params.get("action"):
        filter_kw["action"] = params["action"]
    if params.get("since"):
        filter_kw["since"] = datetime.fromisoformat(params["since"]).replace(tzinfo=timezone.utc)
    if params.get("until"):
        filter_kw["until"] = datetime.fromisoformat(params["until"]).replace(tzinfo=timezone.utc)

    fmt = params.get("format", "csv").lower()
    if fmt == "json":
        rows = await activity_logger.export_rows(**filter_kw)
        return JSONResponse({"rows": rows, "total": len(rows)})

    csv_data = await activity_logger.export_csv(**filter_kw)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


# ------------------------------------------------------------------
# Route list
# ------------------------------------------------------------------

admin_routes = [
    Route("/admin/users", admin_list_users, methods=["GET"]),
    Route("/admin/users/{id}", admin_user_detail, methods=["GET"]),
    Route("/admin/users/{id}/roles", admin_update_user_roles, methods=["POST"]),
    Route("/admin/users/{id}/block", admin_block_user, methods=["POST"]),
    Route("/admin/users/{id}/activate", admin_activate_user, methods=["POST"]),
    Route("/admin/roles", admin_list_roles, methods=["GET"]),
    Route("/admin/roles", admin_create_role, methods=["POST"]),
    Route("/admin/roles/{id}", admin_update_role, methods=["PUT"]),
    Route("/admin/roles/{id}", admin_delete_role, methods=["DELETE"]),
    Route("/admin/permissions", admin_list_permissions, methods=["GET"]),
    Route("/admin/audit/export", admin_audit_export, methods=["GET"]),
    Route("/admin/audit", admin_audit_log, methods=["GET"]),
]
