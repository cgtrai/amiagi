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

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.rbac.middleware import require_permission

logger = logging.getLogger(__name__)


def _get_vault(request: Request):
    """Retrieve SecretVault from app.state or return None."""
    return getattr(request.app.state, "secret_vault", None)


def _get_audit_chain(request: Request):
    """Retrieve AuditChain from app.state or return None."""
    return getattr(request.app.state, "audit_chain", None)


def _get_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return str(user.user_id) if user else "anonymous"


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


# ── GET /api/vault/{agent_id} ────────────────────────────────

@require_permission("vault.admin")
async def vault_agent_keys(request: Request) -> JSONResponse:
    """List secret keys for a specific agent (values masked)."""
    vault = _get_vault(request)
    if vault is None:
        return JSONResponse({"error": "vault_not_configured"}, status_code=503)

    agent_id = request.path_params["agent_id"]

    try:
        keys = await vault.alist_keys(agent_id)
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

    if not key:
        return JSONResponse({"error": "key_required"}, status_code=400)
    if not value:
        return JSONResponse({"error": "value_required"}, status_code=400)

    try:
        await vault.aset_secret(agent_id, key, value)
        await _log_vault_access(request, agent_id, key, "set")

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "vault.set_secret", {
            "agent_id": agent_id,
            "key": key,
        })

        return JSONResponse({
            "ok": True,
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


# ── Route table ──────────────────────────────────────────────

vault_routes: list[Route] = [
    Route("/api/vault", vault_list, methods=["GET"]),
    Route("/api/vault/access-log", vault_access_log, methods=["GET"]),
    Route("/api/vault/{agent_id}", vault_agent_keys, methods=["GET"]),
    Route("/api/vault/{agent_id}", vault_set_secret, methods=["POST"]),
    Route("/api/vault/{agent_id}/{key}", vault_delete_secret, methods=["DELETE"]),
    Route("/api/vault/{agent_id}/{key}/rotate", vault_rotate_secret, methods=["POST"]),
    Route("/api/vault/{agent_id}/{key}/assignments", vault_get_assignments, methods=["GET"]),
    Route("/api/vault/{agent_id}/{key}/assignments", vault_update_assignments, methods=["PUT"]),
]
