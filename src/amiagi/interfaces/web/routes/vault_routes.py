"""Vault (credential management) API routes.

Endpoints:
    GET    /api/vault                             — list all agents with secret keys
    GET    /api/vault/{agent_id}                  — list secret keys for agent
    POST   /api/vault/{agent_id}                  — add/update a secret
    DELETE /api/vault/{agent_id}/{key}            — delete a secret
    POST   /api/vault/{agent_id}/{key}/rotate     — rotate (regenerate) a secret
    GET    /api/vault/access-log                  — access log (audit trail)
    GET    /api/vault/{agent_id}/{key}/assignments — get secret assignments
    PUT    /api/vault/{agent_id}/{key}/assignments — update secret assignments

All endpoints require the ``vault.admin`` permission (RBAC-enforced).

When the vault has a DB backend attached (``vault.has_db``), all operations
use the async API that persists secrets in ``dbo.vault_secrets`` and writes
access events to ``dbo.vault_access_log``.  Otherwise, the file-based JSON
backend is used transparently (backward compatible with CLI mode).
"""

from __future__ import annotations

import logging
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.rbac.middleware import require_permission

logger = logging.getLogger(__name__)


def _make_secret_id(agent_id: str, key: str) -> str:
    return f"{agent_id}:{key}"


def _parse_secret_id(secret_id: str) -> tuple[str, str]:
    if ":" not in secret_id:
        raise ValueError("invalid_secret_id")
    agent_id, key = secret_id.split(":", 1)
    if not agent_id or not key:
        raise ValueError("invalid_secret_id")
    return agent_id, key


def _parse_expires_at(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_expires_at")
    raw = value.strip()
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _get_vault(request: Request):
    """Retrieve SecretVault from app.state or return None."""
    return getattr(request.app.state, "secret_vault", None)


def _get_audit_chain(request: Request):
    """Retrieve AuditChain from app.state or return None."""
    return getattr(request.app.state, "audit_chain", None)


def _get_cron_scheduler(request: Request):
    """Retrieve CronScheduler from app.state or return None."""
    return getattr(request.app.state, "cron_scheduler", None)


def _get_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return str(user.user_id) if user else "anonymous"


def _rotation_job_name(agent_id: str, key: str) -> str:
    return f"vault-rotation:{agent_id}:{key}"


def _rotation_job_description(agent_id: str, key: str) -> str:
    secret_id = _make_secret_id(agent_id, key)
    return (
        "[vault-rotation]\n"
        f"vault_secret_id={secret_id}\n"
        f"agent_id={agent_id}\n"
        f"key={key}"
    )


def _is_rotation_job_for_secret(job: object, agent_id: str, key: str) -> bool:
    name = str(getattr(job, "name", "") or "")
    description = str(getattr(job, "task_description", "") or "")
    return name == _rotation_job_name(agent_id, key) or f"vault_secret_id={_make_secret_id(agent_id, key)}" in description


def _serialize_cron_job(job: object) -> dict[str, object]:
    from amiagi.interfaces.web.scheduling.cron_scheduler import cron_to_human

    if hasattr(job, "to_dict"):
        data = job.to_dict()
    else:
        data = {
            "id": getattr(job, "id", ""),
            "name": getattr(job, "name", ""),
            "cron_expr": getattr(job, "cron_expr", ""),
            "task_title": getattr(job, "task_title", ""),
            "task_description": getattr(job, "task_description", ""),
            "enabled": getattr(job, "enabled", True),
            "last_run": getattr(job, "last_run", None),
            "created_at": getattr(job, "created_at", ""),
            "next_run": getattr(job, "next_run", None),
        }
    cron_expr = str(data.get("cron_expr", "") or "")
    data["human_readable"] = cron_to_human(cron_expr) if cron_expr else ""
    return data


async def _log_vault_access(
    request: Request,
    agent_id: str,
    key: str,
    action: str,
) -> None:
    """Record a vault access event.

    Prefers DB-backed ``vault_access_log`` table when the vault has a
    repository attached.  Falls back to the legacy JSONL AuditChain.
    """
    user_id = _get_user_id(request)

    # Primary: DB access log via VaultRepository
    vault = _get_vault(request)
    if vault is not None and vault.has_db:
        try:
            await vault.alog_access(agent_id, key, f"vault.{action}", user_id)
        except Exception:
            logger.debug("DB vault access log failed — falling back to AuditChain", exc_info=True)

    # Fallback: legacy JSONL AuditChain
    chain = _get_audit_chain(request)
    if chain is not None:
        chain.record_action(
            agent_id=agent_id,
            action=f"vault.{action}",
            target=key,
            approved_by=user_id,
            details={"vault_key": key, "user": user_id},
        )


# ── GET /api/vault ───────────────────────────────────────────

@require_permission("vault.admin")
async def vault_list(request: Request) -> JSONResponse:
    """List all agents that have secrets, with key names (not values)."""
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    try:
        agents = await vault.alist_agents()
        return JSONResponse({"ok": True, "agents": agents})
    except Exception as exc:
        logger.exception("vault.list failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@require_permission("vault.admin")
async def vault_root(request: Request) -> JSONResponse:
    if request.method == "POST":
        return await vault_create_secret(request)
    return await vault_list(request)


# ── GET /api/vault/{agent_id} ────────────────────────────────

@require_permission("vault.admin")
async def vault_agent_keys(request: Request) -> JSONResponse:
    """List secret keys for a specific agent (values masked)."""
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    agent_id = request.path_params["agent_id"]

    try:
        keys = await vault.alist_keys(agent_id, include_metadata=True)
        await _log_vault_access(request, agent_id, "*", "list_keys")
        return JSONResponse({
            "ok": True,
            "agent_id": agent_id,
            "keys": keys,
        })
    except Exception as exc:
        logger.exception("vault.agent_keys failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── POST /api/vault/{agent_id} ───────────────────────────────

@require_permission("vault.admin")
async def vault_set_secret(request: Request) -> JSONResponse:
    """Add or update a secret for an agent.

    Body: { "key": "API_KEY", "value": "sk-..." }
    """
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    agent_id = request.path_params["agent_id"]
    body = await request.json()
    key = body.get("key", "").strip()
    value = body.get("value", "")
    secret_type = str(body.get("type") or body.get("secret_type") or "api_key").strip() or "api_key"

    if not key:
        return JSONResponse({"error": "key_required"}, status_code=400)
    if not value:
        return JSONResponse({"error": "value_required"}, status_code=400)

    try:
        expires_at = _parse_expires_at(body.get("expires_at"))
    except ValueError:
        return JSONResponse({"error": "invalid_expires_at"}, status_code=400)

    try:
        await vault.aset_secret(
            agent_id,
            key,
            value,
            secret_type=secret_type,
            expires_at=expires_at,
        )
        await _log_vault_access(request, agent_id, key, "set")

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "vault.set_secret", {
            "agent_id": agent_id,
            "key": key,
        })

        return JSONResponse({
            "ok": True,
            "id": _make_secret_id(agent_id, key),
            "agent_id": agent_id,
            "key": key,
        }, status_code=201)

    except Exception as exc:
        logger.exception("vault.set_secret failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── DELETE /api/vault/{agent_id}/{key} ───────────────────────

@require_permission("vault.admin")
async def vault_delete_secret(request: Request) -> JSONResponse:
    """Delete a secret for an agent."""
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    agent_id = request.path_params["agent_id"]
    key = request.path_params["key"]

    try:
        removed = await vault.adelete_secret(agent_id, key)
        if not removed:
            return JSONResponse({"error": "not_found"}, status_code=404)

        await _log_vault_access(request, agent_id, key, "delete")

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "vault.delete_secret", {
            "agent_id": agent_id,
            "key": key,
        })

        return JSONResponse({"ok": True, "agent_id": agent_id, "key": key})

    except Exception as exc:
        logger.exception("vault.delete_secret failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── POST /api/vault/{agent_id}/{key}/rotate ──────────────────

@require_permission("vault.admin")
async def vault_rotate_secret(request: Request) -> JSONResponse:
    """Rotate a secret — set a new value for an existing key.

    Body: { "value": "new-sk-..." }
    """
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    agent_id = request.path_params["agent_id"]
    key = request.path_params["key"]
    body = await request.json()
    new_value = body.get("value", "")

    if not new_value:
        return JSONResponse({"error": "value_required"}, status_code=400)

    # Verify key exists (use async API)
    existing = await vault.aget_secret(agent_id, key)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        await vault.arotate_secret(agent_id, key, new_value)
        await _log_vault_access(request, agent_id, key, "rotate")

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "vault.rotate_secret", {
            "agent_id": agent_id,
            "key": key,
        })

        return JSONResponse({"ok": True, "agent_id": agent_id, "key": key})

    except Exception as exc:
        logger.exception("vault.rotate_secret failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── GET /api/vault/access-log ────────────────────────────────

@require_permission("vault.admin")
async def vault_access_log(request: Request) -> JSONResponse:
    """Return recent vault access log entries.

    Prefers DB-backed ``dbo.vault_access_log`` when the vault has a
    repository.  Falls back to the legacy JSONL AuditChain.
    """
    limit = int(request.query_params.get("limit", "50"))

    # Primary: DB access log
    vault = _get_vault(request)
    if vault is not None and vault.has_db:
        try:
            entries = await vault.aget_access_log(limit=limit)
            return JSONResponse({"ok": True, "entries": entries, "total": len(entries)})
        except Exception:
            logger.debug("DB access log query failed — falling back", exc_info=True)

    # Fallback: legacy AuditChain (JSONL)
    chain = _get_audit_chain(request)
    if chain is None:
        return JSONResponse({"ok": True, "entries": [], "total": 0})

    entries = chain.query(action="vault.set", limit=limit)

    # Also include other vault actions
    for action in ("vault.list_keys", "vault.delete", "vault.rotate"):
        entries.extend(chain.query(action=action, limit=limit))

    # Sort newest-first and limit
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    entries = entries[:limit]

    serialized = [
        {
            "timestamp": e.timestamp,
            "agent_id": e.agent_id,
            "key": e.target,
            "action": e.action,
            "user": e.approved_by,
        }
        for e in entries
    ]

    return JSONResponse({"ok": True, "entries": serialized, "total": len(serialized)})


@require_permission("vault.admin")
async def vault_create_secret(request: Request) -> JSONResponse:
    body = await request.json()
    agent_id = str(body.get("agent_id") or "").strip()
    if not agent_id:
        return JSONResponse({"error": "agent_id_required"}, status_code=400)
    request.path_params["agent_id"] = agent_id
    return await vault_set_secret(request)


@require_permission("vault.admin")
async def vault_update_secret(request: Request) -> JSONResponse:
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    body = await request.json()
    value = body.get("value", "")
    if not value:
        return JSONResponse({"error": "value_required"}, status_code=400)
    secret_type = str(body.get("type") or body.get("secret_type") or "api_key").strip() or "api_key"
    try:
        expires_at = _parse_expires_at(body.get("expires_at"))
    except ValueError:
        return JSONResponse({"error": "invalid_expires_at"}, status_code=400)

    existing = await vault.aget_secret(agent_id, key)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await vault.aset_secret(
        agent_id,
        key,
        value,
        secret_type=secret_type,
        expires_at=expires_at,
    )
    await _log_vault_access(request, agent_id, key, "update")
    return JSONResponse({"ok": True, "id": _make_secret_id(agent_id, key), "agent_id": agent_id, "key": key})


@require_permission("vault.admin")
async def vault_delete_secret_alias(request: Request) -> JSONResponse:
    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    request.path_params["agent_id"] = agent_id
    request.path_params["key"] = key
    return await vault_delete_secret(request)


@require_permission("vault.admin")
async def vault_rotate_secret_alias(request: Request) -> JSONResponse:
    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    request.path_params["agent_id"] = agent_id
    request.path_params["key"] = key
    return await vault_rotate_secret(request)


@require_permission("vault.admin")
async def vault_secret_access_log(request: Request) -> JSONResponse:
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    limit = int(request.query_params.get("limit", "20"))
    agent_id = request.path_params["agent_id"]
    key = request.path_params["key"]

    if await vault.aget_secret(agent_id, key) is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if getattr(vault, "has_db", False):
        entries = await vault.aget_secret_access_log(agent_id, key, limit=limit)
        return JSONResponse({"ok": True, "entries": entries, "total": len(entries)})

    chain = _get_audit_chain(request)
    if chain is None:
        return JSONResponse({"ok": True, "entries": [], "total": 0})

    entries = []
    for action in ("vault.set", "vault.update", "vault.delete", "vault.rotate", "vault.update_assignments"):
        for item in chain.query(action=action, limit=limit):
            if item.agent_id == agent_id and item.target == key:
                entries.append(
                    {
                        "timestamp": item.timestamp,
                        "agent_id": item.agent_id,
                        "key": item.target,
                        "action": item.action,
                        "user": item.approved_by,
                    }
                )
    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    entries = entries[:limit]
    return JSONResponse({"ok": True, "entries": entries, "total": len(entries)})


@require_permission("vault.admin")
async def vault_secret_access_log_alias(request: Request) -> JSONResponse:
    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    request.path_params["agent_id"] = agent_id
    request.path_params["key"] = key
    return await vault_secret_access_log(request)


@require_permission("vault.admin")
async def vault_rotation_schedule(request: Request) -> JSONResponse:
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    scheduler = _get_cron_scheduler(request)
    if scheduler is None:
        return JSONResponse({"error": "scheduler_unavailable"}, status_code=503)

    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if await vault.aget_secret(agent_id, key) is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    jobs = [_serialize_cron_job(job) for job in scheduler.list_jobs() if _is_rotation_job_for_secret(job, agent_id, key)]
    jobs.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
    return JSONResponse({
        "ok": True,
        "secret_id": _make_secret_id(agent_id, key),
        "jobs": jobs,
        "total": len(jobs),
    })


@require_permission("vault.admin")
async def vault_create_rotation_schedule(request: Request) -> JSONResponse:
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    scheduler = _get_cron_scheduler(request)
    if scheduler is None:
        return JSONResponse({"error": "scheduler_unavailable"}, status_code=503)

    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if await vault.aget_secret(agent_id, key) is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    body = await request.json()
    schedule = body.get("schedule") or body.get("schedule_builder")
    cron_expr = body.get("cron_expr") or body.get("cron_expression")
    if schedule:
        from amiagi.interfaces.web.scheduling.cron_scheduler import build_cron_expression

        try:
            cron_expr = build_cron_expression(schedule if isinstance(schedule, dict) else {})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    cron_expr = str(cron_expr or "").strip()
    if not cron_expr:
        return JSONResponse({"error": "cron_expr_required"}, status_code=400)

    existing_jobs = [job for job in scheduler.list_jobs() if _is_rotation_job_for_secret(job, agent_id, key)]
    if existing_jobs:
        return JSONResponse({"error": "rotation_schedule_exists"}, status_code=409)

    from amiagi.interfaces.web.scheduling.cron_scheduler import CronJob

    job = CronJob(
        name=_rotation_job_name(agent_id, key),
        cron_expr=cron_expr,
        task_title=str(body.get("task_title") or f"Rotate vault secret {agent_id}/{key}"),
        task_description=str(body.get("task_description") or _rotation_job_description(agent_id, key)),
        enabled=bool(body.get("enabled", True)),
    )

    try:
        created = await scheduler.create_job(job)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    await _log_vault_access(request, agent_id, key, "schedule_rotation")

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "vault.schedule_rotation", {
        "agent_id": agent_id,
        "key": key,
        "cron_expr": created.cron_expr,
    })

    return JSONResponse({
        "ok": True,
        "secret_id": _make_secret_id(agent_id, key),
        "job": _serialize_cron_job(created),
    }, status_code=201)


@require_permission("vault.admin")
async def vault_delete_rotation_schedule(request: Request) -> JSONResponse:
    scheduler = _get_cron_scheduler(request)
    if scheduler is None:
        return JSONResponse({"error": "scheduler_unavailable"}, status_code=503)

    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    job_id = request.path_params["job_id"]
    matched_job = next((job for job in scheduler.list_jobs() if getattr(job, "id", None) == job_id and _is_rotation_job_for_secret(job, agent_id, key)), None)
    if matched_job is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    deleted = await scheduler.delete_job(job_id)
    if not deleted:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await _log_vault_access(request, agent_id, key, "delete_rotation_schedule")

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "vault.delete_rotation_schedule", {
        "agent_id": agent_id,
        "key": key,
        "job_id": job_id,
    })

    return JSONResponse({"ok": True, "secret_id": _make_secret_id(agent_id, key), "job_id": job_id})


# ── GET /api/vault/{agent_id}/{key}/assignments ──────────────

@require_permission("vault.admin")
async def vault_get_assignments(request: Request) -> JSONResponse:
    """Return the list of entities (agents/skills) assigned to this secret."""
    agent_id = request.path_params["agent_id"]
    key = request.path_params["key"]

    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    # Verify key exists (async — reads from DB or cache)
    existing = await vault.aget_secret(agent_id, key)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        # Fallback: file-based vault has implicit per-agent assignment
        return JSONResponse({
            "ok": True,
            "agent_id": agent_id,
            "key": key,
            "assignments": [{"entity_type": "agent", "entity_id": agent_id}],
        })

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT entity_type, entity_id, assigned_at, assigned_by
                   FROM dbo.vault_assignments
                   WHERE secret_agent_id = $1 AND secret_key = $2
                   ORDER BY assigned_at""",
                agent_id,
                key,
            )
        result = [
            {
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "assigned_at": str(r["assigned_at"]),
                "assigned_by": r.get("assigned_by", ""),
            }
            for r in rows
        ]
        return JSONResponse({
            "ok": True,
            "agent_id": agent_id,
            "key": key,
            "assignments": result,
        })
    except Exception as exc:
        logger.exception("vault.get_assignments failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── PUT /api/vault/{agent_id}/{key}/assignments ──────────────

@require_permission("vault.admin")
async def vault_update_assignments(request: Request) -> JSONResponse:
    """Update (replace) the assignments for a secret.

    Body: {
        "assignments": [
            {"entity_type": "agent", "entity_id": "kastor"},
            {"entity_type": "skill", "entity_id": "code_gen"}
        ]
    }
    """
    agent_id = request.path_params["agent_id"]
    key = request.path_params["key"]
    body = await request.json()

    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    # Verify key exists (async — reads from DB or cache)
    existing = await vault.aget_secret(agent_id, key)
    if existing is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    assignments = body.get("assignments", [])
    if not isinstance(assignments, list):
        return JSONResponse({"error": "assignments_must_be_list"}, status_code=400)

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return JSONResponse({"error": "db_not_available"}, status_code=503)

    user_id = _get_user_id(request)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Delete existing assignments
                await conn.execute(
                    """DELETE FROM dbo.vault_assignments
                       WHERE secret_agent_id = $1 AND secret_key = $2""",
                    agent_id,
                    key,
                )
                # Insert new ones
                for asgn in assignments:
                    etype = asgn.get("entity_type", "")
                    eid = asgn.get("entity_id", "")
                    if etype not in ("agent", "skill") or not eid:
                        continue
                    await conn.execute(
                        """INSERT INTO dbo.vault_assignments
                               (secret_agent_id, secret_key, entity_type, entity_id, assigned_by)
                           VALUES ($1, $2, $3, $4, $5)
                           ON CONFLICT DO NOTHING""",
                        agent_id,
                        key,
                        etype,
                        eid,
                        user_id,
                    )

        await _log_vault_access(request, agent_id, key, "update_assignments")

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "vault.update_assignments", {
            "agent_id": agent_id,
            "key": key,
            "count": len(assignments),
        })

        return JSONResponse({"ok": True, "agent_id": agent_id, "key": key})

    except Exception as exc:
        logger.exception("vault.update_assignments failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@require_permission("vault.admin")
async def vault_get_assignments_alias(request: Request) -> JSONResponse:
    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    request.path_params["agent_id"] = agent_id
    request.path_params["key"] = key
    return await vault_get_assignments(request)


@require_permission("vault.admin")
async def vault_update_assignments_alias(request: Request) -> JSONResponse:
    try:
        agent_id, key = _parse_secret_id(request.path_params["secret_id"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    request.path_params["agent_id"] = agent_id
    request.path_params["key"] = key
    return await vault_update_assignments(request)


# ── Route table ──────────────────────────────────────────────

vault_routes: list[Route] = [
    Route("/api/vault", vault_root, methods=["GET", "POST"]),
    Route("/api/vault/access-log", vault_access_log, methods=["GET"]),
    Route("/api/vault/{agent_id}", vault_agent_keys, methods=["GET"]),
    Route("/api/vault/{agent_id}", vault_set_secret, methods=["POST"]),
    Route("/api/vault/{secret_id}", vault_update_secret, methods=["PUT"]),
    Route("/api/vault/{secret_id}", vault_delete_secret_alias, methods=["DELETE"]),
    Route("/api/vault/{secret_id}/rotation-schedule", vault_rotation_schedule, methods=["GET"]),
    Route("/api/vault/{secret_id}/rotation-schedule", vault_create_rotation_schedule, methods=["POST"]),
    Route("/api/vault/{secret_id}/rotation-schedule/{job_id}", vault_delete_rotation_schedule, methods=["DELETE"]),
    Route("/api/vault/{agent_id}/{key}", vault_delete_secret, methods=["DELETE"]),
    Route("/api/vault/{secret_id}/rotate", vault_rotate_secret_alias, methods=["PUT"]),
    Route("/api/vault/{agent_id}/{key}/rotate", vault_rotate_secret, methods=["POST"]),
    Route("/api/vault/{secret_id}/access-log", vault_secret_access_log_alias, methods=["GET"]),
    Route("/api/vault/{agent_id}/{key}/access-log", vault_secret_access_log, methods=["GET"]),
    Route("/api/vault/{secret_id}/assignments", vault_get_assignments_alias, methods=["GET"]),
    Route("/api/vault/{secret_id}/assignments", vault_update_assignments_alias, methods=["PUT"]),
    Route("/api/vault/{agent_id}/{key}/assignments", vault_get_assignments, methods=["GET"]),
    Route("/api/vault/{agent_id}/{key}/assignments", vault_update_assignments, methods=["PUT"]),
]
