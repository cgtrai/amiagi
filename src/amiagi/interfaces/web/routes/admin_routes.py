"""Admin panel routes — user and role management."""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import io
import logging
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from amiagi.interfaces.web.rbac.middleware import require_permission
from amiagi.interfaces.web.audit.log_helpers import log_action

logger = logging.getLogger(__name__)


def _repo_supports(repo: object, method_name: str) -> bool:
    return callable(getattr(type(repo), method_name, None))


def _serialise_role(role) -> dict[str, object]:
    return {
        "id": str(role.id),
        "name": role.name,
        "description": role.description,
        "is_system": getattr(role, "is_system", False),
        "permissions": [p.codename for p in getattr(role, "permissions", [])],
    }


def _serialise_permission(permission) -> dict[str, object]:
    return {
        "id": str(permission.id),
        "codename": permission.codename,
        "description": permission.description,
        "category": permission.category,
    }


def _serialise_activity_rows(rows: list[dict]) -> list[dict[str, object]]:
    serialised: list[dict[str, object]] = []
    for row in rows:
        created_at = row.get("created_at") if isinstance(row, dict) else None
        serialised.append({
            "id": str(row.get("id")) if row.get("id") is not None else None,
            "action": row.get("action"),
            "detail": row.get("detail"),
            "session_id": str(row.get("session_id")) if row.get("session_id") else None,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        })
    return serialised


async def _load_recent_user_activity(request: Request, user_id: UUID, *, limit: int = 5) -> list[dict[str, object]]:
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is None or not hasattr(activity_logger, "query"):
        return []
    try:
        rows = await activity_logger.query(user_id=str(user_id), limit=limit, offset=0)
    except Exception:
        logger.debug("admin.user_activity_preview.failed", exc_info=True)
        return []
    return _serialise_activity_rows(rows)


def _serialise_user(user, *, roles: list | None = None, recent_activity: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "provider": getattr(user, "provider", ""),
        "is_active": user.is_active,
        "is_blocked": user.is_blocked,
        "roles": [_serialise_role(r) for r in user.roles],
        "permissions": user.permissions,
        "all_roles": [_serialise_role(r) for r in (roles or [])],
        "audit_url": f"/admin/audit?user={user.id}",
        "recent_activity": recent_activity or [],
    }


def _parse_audit_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _audit_action_mode(action: str | None) -> str:
    if not action:
        return "exact"
    return "contains" if "." not in action else "exact"


def _audit_filters_from_request(request: Request) -> tuple[dict, int, int]:
    params = request.query_params
    page = max(int(params.get("page", "1")), 1)
    per_page = max(int(params.get("per_page") or params.get("limit") or "50"), 1)
    filter_kw: dict = {
        "user_id": params.get("user") or None,
        "action": params.get("action") or None,
        "action_match": _audit_action_mode(params.get("action")),
        "since": _parse_audit_date(params.get("since")),
        "until": _parse_audit_date(params.get("until")),
        "session_id": params.get("session_id") or None,
        "search": params.get("q") or None,
        "error_only": params.get("error_only", "").lower() in {"1", "true", "yes", "on"},
    }
    return filter_kw, page, per_page


def _activity_logger_retention_days(activity_logger) -> int | None:
    return getattr(activity_logger, "_retention_days", None)


def _sync_persisted_audit_retention(request: Request, activity_logger) -> int | None:
    store = getattr(request.app.state, "audit_retention_store", None)
    if store is None:
        return _activity_logger_retention_days(activity_logger)
    try:
        found, persisted_days = store.load()
    except Exception:
        return _activity_logger_retention_days(activity_logger)
    if found:
        activity_logger._retention_days = persisted_days
        return persisted_days
    return _activity_logger_retention_days(activity_logger)


def _normalise_retention_days(value) -> int | None:
    if value in (None, "", "forever", "none", 0, "0"):
        return None
    days = int(value)
    if days <= 0:
        return None
    return days


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
    recent_activity = await _load_recent_user_activity(request, user_id)

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/user_detail.html",
            {"target_user": user, "all_roles": roles, "user": request.state.user},
        )

    return JSONResponse({
        **_serialise_user(user, roles=roles, recent_activity=recent_activity),
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

    updated_user = await repo.get_user_by_id(user_id)
    all_roles = await repo.list_roles()
    await log_action(request, "admin.update_roles", {"target_user_id": str(user_id), "role_ids": role_ids})
    return JSONResponse({
        "status": "ok",
        "user": _serialise_user(updated_user or user, roles=all_roles),
    })


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
async def admin_role_detail(request: Request) -> Response:
    """GET /admin/roles/{id} — single role detail for composer drawer."""
    repo = request.app.state.rbac_repo
    role_id = UUID(request.path_params["id"])

    role = None
    if _repo_supports(repo, "get_role"):
        role = await repo.get_role(role_id)
    if role is None:
        roles = await repo.list_roles()
        role = next((item for item in roles if item.id == role_id), None)
    if role is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    permissions = await repo.list_permissions()
    grouped_permissions: dict[str, list[dict[str, object]]] = {}
    for permission in permissions:
        grouped_permissions.setdefault(permission.category or "general", []).append(_serialise_permission(permission))
    grouped_permissions = {
        key: sorted(value, key=lambda item: str(item.get("codename") or ""))
        for key, value in sorted(grouped_permissions.items(), key=lambda item: item[0])
    }

    payload = {
        **_serialise_role(role),
        "permission_ids": [str(permission.id) for permission in getattr(role, "permissions", [])],
        "permissions_detail": [_serialise_permission(permission) for permission in getattr(role, "permissions", [])],
    }
    return JSONResponse({
        "role": payload,
        "all_permissions": [_serialise_permission(permission) for permission in permissions],
        "permission_groups": grouped_permissions,
    })


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


@require_permission("admin.roles")
async def admin_permissions_export(request: Request) -> Response:
    """GET /admin/permissions/export — export RBAC matrix as CSV or JSON."""
    repo = request.app.state.rbac_repo
    fmt = (request.query_params.get("format") or "csv").lower()
    roles = await repo.list_roles()
    permissions = await repo.list_permissions()

    rows = []
    for permission in permissions:
        row = {
            "permission": permission.codename,
            "category": permission.category,
            "description": permission.description,
        }
        for role in roles:
            row[role.name] = permission.codename in [perm.codename for perm in getattr(role, "permissions", [])]
        rows.append(row)

    if fmt == "json":
        return JSONResponse({
            "roles": [_serialise_role(role) for role in roles],
            "rows": rows,
            "total": len(rows),
        })

    stream = io.StringIO()
    fieldnames = ["permission", "category", "description", *[role.name for role in roles]]
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            **row,
            **{role.name: "yes" if row.get(role.name) else "" for role in roles},
        })
    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rbac_matrix.csv"},
    )


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
    filter_kw, page, per_page = _audit_filters_from_request(request)

    rows = await activity_logger.query(**filter_kw, limit=per_page, offset=(page - 1) * per_page)
    total = await activity_logger.count(**filter_kw)
    retention_days = _sync_persisted_audit_retention(request, activity_logger)

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/audit.html",
            {
                "logs": rows,
                "total": total,
                "page": page,
                "per_page": per_page,
                "filters": dict(request.query_params),
                "retention_days": retention_days,
                "user": request.state.user,
            },
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
        "logs": [
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
        "per_page": per_page,
        "retention_days": retention_days,
    })


async def _audit_fallback(request: Request) -> Response:
    """Fallback audit endpoint using raw SQL (no WebActivityLogger)."""
    pool = request.app.state.db_pool
    params = request.query_params
    filter_kw, page, per_page = _audit_filters_from_request(request)
    offset = (page - 1) * per_page

    conditions: list[str] = []
    sql_params: list[object] = []
    idx = 1

    if filter_kw.get("user_id"):
        conditions.append(f"al.user_id = ${idx}")
        sql_params.append(filter_kw["user_id"])
        idx += 1
    if filter_kw.get("action"):
        if filter_kw.get("action_match") == "contains":
            conditions.append(f"LOWER(al.action) LIKE LOWER(${idx})")
            sql_params.append(f"%{filter_kw['action']}%")
        else:
            conditions.append(f"al.action = ${idx}")
            sql_params.append(filter_kw["action"])
        idx += 1
    if filter_kw.get("since"):
        conditions.append(f"al.created_at >= ${idx}")
        sql_params.append(filter_kw["since"])
        idx += 1
    if filter_kw.get("until"):
        conditions.append(f"al.created_at <= ${idx}")
        sql_params.append(filter_kw["until"])
        idx += 1
    if filter_kw.get("session_id"):
        conditions.append(f"al.session_id = ${idx}")
        sql_params.append(filter_kw["session_id"])
        idx += 1
    if filter_kw.get("search"):
        conditions.append(
            "(" 
            f"CAST(al.user_id AS text) ILIKE ${idx} OR "
            f"CAST(al.session_id AS text) ILIKE ${idx} OR "
            f"u.email ILIKE ${idx} OR "
            f"al.action ILIKE ${idx} OR "
            f"CAST(al.detail AS text) ILIKE ${idx} OR "
            f"CAST(al.ip_address AS text) ILIKE ${idx}" 
            ")"
        )
        sql_params.append(f"%{filter_kw['search']}%")
        idx += 1
    if filter_kw.get("error_only"):
        conditions.append(
            "(" 
            "LOWER(al.action) LIKE '%error%' OR "
            "LOWER(CAST(al.detail AS text)) LIKE '%error%' OR "
            "LOWER(CAST(al.detail AS text)) LIKE '%fail%' OR "
            "LOWER(CAST(al.detail AS text)) LIKE '%exception%'"
            ")"
        )

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM user_activity_log al LEFT JOIN users u ON u.id = al.user_id {where}",
            *sql_params,
        )
        rows = await conn.fetch(
            f"""
            SELECT al.id, al.user_id, al.session_id, u.email, al.action, al.detail, al.ip_address, al.created_at
            FROM user_activity_log al
            LEFT JOIN users u ON u.id = al.user_id
            {where}
            ORDER BY al.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *sql_params,
            per_page,
            offset,
        )

    accept = request.headers.get("accept", "")
    templates = getattr(request.app.state, "templates", None)
    if templates is not None and "text/html" in accept:
        return templates.TemplateResponse(
            request,
            "admin/audit.html",
            {
                "logs": rows,
                "total": total,
                "page": page,
                "per_page": per_page,
                "filters": dict(params),
                "retention_days": None,
                "user": request.state.user,
            },
        )

    return JSONResponse({
        "items": [
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]) if r["user_id"] else None,
                "session_id": str(r["session_id"]) if r.get("session_id") else None,
                "email": r["email"],
                "action": r["action"],
                "detail": r["detail"],
                "ip_address": r["ip_address"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "logs": [
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]) if r["user_id"] else None,
                "session_id": str(r["session_id"]) if r.get("session_id") else None,
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
        "per_page": per_page,
        "retention_days": None,
    })


@require_permission("admin.audit")
async def admin_update_audit_retention(request: Request) -> Response:
    """PUT /admin/audit/retention — update and persist audit retention window."""
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is None:
        return JSONResponse({"error": "activity_logger_not_available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        retention_days = _normalise_retention_days(body.get("retention_days"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid_retention_days"}, status_code=400)

    store = getattr(request.app.state, "audit_retention_store", None)
    if store is not None:
        try:
            store.save(retention_days)
        except Exception:
            return JSONResponse({"error": "retention_persist_failed"}, status_code=500)

    activity_logger._retention_days = retention_days
    await log_action(request, "admin.audit.retention.updated", {
        "retention_days": retention_days,
    })
    return JSONResponse({
        "ok": True,
        "retention_days": retention_days,
        "retention_label": "forever" if retention_days is None else f"{retention_days}d",
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

    params = request.query_params
    filter_kw, _page, _per_page = _audit_filters_from_request(request)
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
# Bulk operations (R1)
# ------------------------------------------------------------------

@require_permission("admin.users")
async def admin_bulk_users(request: Request) -> Response:
    """POST /admin/users/bulk — bulk activate/block users."""
    repo = request.app.state.rbac_repo
    body = await request.json()
    action = body.get("action", "")
    user_ids = body.get("user_ids", [])

    if action not in ("activate", "block"):
        return JSONResponse({"error": "invalid_action"}, status_code=400)
    if not user_ids:
        return JSONResponse({"error": "no_users"}, status_code=400)

    count = 0
    session_manager = getattr(request.app.state, "session_manager", None)
    for uid_str in user_ids:
        uid = UUID(uid_str)
        if action == "activate":
            ok = await repo.activate_user(uid)
        else:
            ok = await repo.block_user(uid)
            if ok and session_manager is not None and hasattr(session_manager, "revoke_all_user_sessions"):
                await session_manager.revoke_all_user_sessions(uid)
        if ok:
            count += 1

    await log_action(request, f"admin.bulk_{action}", {"count": count, "user_ids": user_ids})
    return JSONResponse({"ok": True, "action": action, "affected": count})


# ------------------------------------------------------------------
# Invite (R2)
# ------------------------------------------------------------------

@require_permission("admin.users")
async def admin_invite_user(request: Request) -> Response:
    """POST /admin/users/invite — invite user by email."""
    repo = request.app.state.rbac_repo
    body = await request.json()
    email = (body.get("email") or "").strip()
    if not email:
        return JSONResponse({"error": "email_required"}, status_code=400)

    role_id = body.get("role_id")

    # Create user or send invite depending on repo capabilities
    if _repo_supports(repo, "invite_user"):
        result = await repo.invite_user(email, role_id=role_id)
    elif _repo_supports(repo, "create_user"):
        result = await repo.create_user(
            email=email,
            display_name=email.split("@")[0],
            is_active=False,
            provider="invite",
            provider_sub=f"invite:{email}",
        )
        if role_id:
            await repo.assign_role(result.id, UUID(role_id))
    elif _repo_supports(repo, "upsert_user_from_oauth"):
        result = await repo.upsert_user_from_oauth(
            email=email,
            display_name=email.split("@")[0],
            avatar_url=None,
            provider="invite",
            provider_sub=f"invite:{email}",
        )
        await repo.update_user(result.id, is_active=False)
        if role_id:
            await repo.assign_role(result.id, UUID(role_id))
    else:
        return JSONResponse({"error": "invite_not_supported"}, status_code=503)

    await log_action(request, "admin.invite_user", {"email": email, "role_id": role_id})
    return JSONResponse({
        "ok": True,
        "email": email,
        "user_id": str(getattr(result, "id", "")) if getattr(result, "id", None) else None,
        "role_id": role_id,
    }, status_code=201)


# ------------------------------------------------------------------
# Route list
# ------------------------------------------------------------------

admin_routes = [
    Route("/admin/users/bulk", admin_bulk_users, methods=["POST"]),
    Route("/admin/users/invite", admin_invite_user, methods=["POST"]),
    Route("/admin/users", admin_list_users, methods=["GET"]),
    Route("/admin/users/{id}", admin_user_detail, methods=["GET"]),
    Route("/admin/users/{id}/roles", admin_update_user_roles, methods=["POST"]),
    Route("/admin/users/{id}/block", admin_block_user, methods=["POST"]),
    Route("/admin/users/{id}/activate", admin_activate_user, methods=["POST"]),
    Route("/admin/roles", admin_list_roles, methods=["GET"]),
    Route("/admin/roles", admin_create_role, methods=["POST"]),
    Route("/admin/roles/{id}", admin_role_detail, methods=["GET"]),
    Route("/admin/roles/{id}", admin_update_role, methods=["PUT"]),
    Route("/admin/roles/{id}", admin_delete_role, methods=["DELETE"]),
    Route("/admin/permissions", admin_list_permissions, methods=["GET"]),
    Route("/admin/permissions/export", admin_permissions_export, methods=["GET"]),
    Route("/admin/audit/retention", admin_update_audit_retention, methods=["PUT"]),
    Route("/admin/audit/export", admin_audit_export, methods=["GET"]),
    Route("/admin/audit", admin_audit_log, methods=["GET"]),
]
