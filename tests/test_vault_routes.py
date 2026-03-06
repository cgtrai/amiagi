"""Tests for Vault (credential management) routes.

All vault endpoints are guarded by ``@require_permission("vault.admin")``.
Tests inject a mock user with the correct permissions via a simple Starlette
middleware that sets ``request.state.user``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.vault_routes import vault_routes

_LOG_ACTION = "amiagi.interfaces.web.audit.log_helpers.log_action"


# ── Fake user for RBAC ───────────────────────────────────────

@dataclass
class _FakeUser:
    user_id: str = "test-admin"
    email: str = "admin@test.local"
    permissions: list[str] = field(default_factory=lambda: ["vault.admin"])


class _InjectUserMiddleware(BaseHTTPMiddleware):
    """Inject a fake user into request.state for RBAC-protected routes."""

    def __init__(self, app, user=None):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._user is not None:
            request.state.user = self._user
        return await call_next(request)


# ── Helpers ──────────────────────────────────────────────────

def _make_app(
    *,
    user: _FakeUser | None = _FakeUser(),
    **state_attrs: Any,
) -> TestClient:
    middleware = []
    if user is not None:
        middleware.append(Middleware(_InjectUserMiddleware, user=user))
    app = Starlette(routes=list(vault_routes), middleware=middleware)
    for k, v in state_attrs.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def _mock_vault(
    agents: dict[str, dict[str, str]] | None = None,
) -> MagicMock:
    """Build a mock SecretVault with both sync and async APIs."""
    data = agents or {
        "kastor": {"API_KEY": "sk-abc123", "DB_PASSWORD": "p4ss"},
        "polluks": {"OPENAI_KEY": "sk-xyz"},
    }
    vault = MagicMock()

    # Sync API (backward compat)
    vault.list_agents.return_value = [
        {"agent_id": aid, "keys": list(secs.keys()), "count": len(secs)}
        for aid, secs in data.items()
    ]
    vault.list_keys.side_effect = lambda agent_id: list(data.get(agent_id, {}).keys())
    vault.get_secret.side_effect = lambda agent_id, key: data.get(agent_id, {}).get(key)
    vault.set_secret.return_value = None
    vault.delete_secret.side_effect = lambda agent_id, key: key in data.get(agent_id, {})

    # Async API (DB-aware routes use these)
    vault.alist_agents = AsyncMock(return_value=[
        {"agent_id": aid, "keys": list(secs.keys()), "count": len(secs)}
        for aid, secs in data.items()
    ])
    vault.alist_keys = AsyncMock(side_effect=lambda agent_id: list(data.get(agent_id, {}).keys()))
    vault.aget_secret = AsyncMock(side_effect=lambda agent_id, key: data.get(agent_id, {}).get(key))
    vault.aset_secret = AsyncMock(return_value=None)
    vault.adelete_secret = AsyncMock(side_effect=lambda agent_id, key: key in data.get(agent_id, {}))
    vault.arotate_secret = AsyncMock(return_value=True)
    vault.alog_access = AsyncMock(return_value=None)
    vault.aget_access_log = AsyncMock(return_value=[])

    # DB status
    vault.has_db = False

    return vault


def _mock_audit_chain() -> MagicMock:
    chain = MagicMock()
    chain.record_action.return_value = None
    chain.query.return_value = []
    return chain


# ── GET /api/vault ───────────────────────────────────────────

class TestVaultList:
    def test_list_agents(self) -> None:
        vault = _mock_vault()
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.get("/api/vault")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        # alist_agents() returns list of dicts with agent_id, keys, count
        agent_ids = [a["agent_id"] for a in data["agents"]]
        assert "kastor" in agent_ids
        assert "polluks" in agent_ids

    def test_no_vault_returns_503(self) -> None:
        client = _make_app()
        r = client.get("/api/vault")
        assert r.status_code == 503

    def test_unauthenticated_returns_401(self) -> None:
        vault = _mock_vault()
        client = _make_app(user=None, secret_vault=vault)
        r = client.get("/api/vault")
        assert r.status_code == 401


# ── GET /api/vault/{agent_id} ────────────────────────────────

class TestVaultAgentKeys:
    def test_list_keys_for_agent(self) -> None:
        vault = _mock_vault()
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.get("/api/vault/kastor")
        assert r.status_code == 200
        data = r.json()
        assert "API_KEY" in data["keys"]
        assert "DB_PASSWORD" in data["keys"]


# ── POST /api/vault/{agent_id} ───────────────────────────────

class TestVaultSetSecret:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_set_secret(self, _mock_log) -> None:
        vault = _mock_vault()
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.post("/api/vault/kastor", json={"key": "NEW_KEY", "value": "new-val"})
        assert r.status_code == 201
        vault.aset_secret.assert_called_once_with("kastor", "NEW_KEY", "new-val")

    def test_set_secret_missing_key(self) -> None:
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.post("/api/vault/kastor", json={"value": "val"})
        assert r.status_code == 400

    def test_set_secret_missing_value(self) -> None:
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.post("/api/vault/kastor", json={"key": "K"})
        assert r.status_code == 400


# ── DELETE /api/vault/{agent_id}/{key} ───────────────────────

class TestVaultDelete:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_delete_existing(self, _mock_log) -> None:
        vault = _mock_vault()
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.delete("/api/vault/kastor/API_KEY")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_nonexistent_returns_404(self) -> None:
        vault = _mock_vault()
        vault.adelete_secret = AsyncMock(return_value=False)
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.delete("/api/vault/kastor/NONEXISTENT")
        assert r.status_code == 404


# ── POST /api/vault/{agent_id}/{key}/rotate ──────────────────

class TestVaultRotate:
    @patch(_LOG_ACTION, new_callable=AsyncMock)
    def test_rotate_success(self, _mock_log) -> None:
        vault = _mock_vault()
        chain = _mock_audit_chain()
        client = _make_app(secret_vault=vault, audit_chain=chain)
        r = client.post(
            "/api/vault/kastor/API_KEY/rotate",
            json={"value": "new-sk-rotated"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        vault.arotate_secret.assert_called_once_with("kastor", "API_KEY", "new-sk-rotated")

    def test_rotate_empty_value_returns_400(self) -> None:
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.post(
            "/api/vault/kastor/API_KEY/rotate",
            json={"value": ""},
        )
        assert r.status_code == 400

    def test_rotate_nonexistent_returns_404(self) -> None:
        vault = _mock_vault(agents={"kastor": {}})
        client = _make_app(secret_vault=vault)
        r = client.post(
            "/api/vault/kastor/MISSING/rotate",
            json={"value": "val"},
        )
        assert r.status_code == 404


# ── GET /api/vault/access-log ────────────────────────────────

class TestVaultAccessLog:
    def test_log_empty(self) -> None:
        chain = _mock_audit_chain()
        client = _make_app(audit_chain=chain, secret_vault=_mock_vault())
        r = client.get("/api/vault/access-log")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["entries"], list)

    def test_log_no_chain_returns_empty(self) -> None:
        client = _make_app(secret_vault=_mock_vault())
        r = client.get("/api/vault/access-log")
        assert r.status_code == 200
        data = r.json()
        assert data["entries"] == []


# ── GET /api/vault/{agent_id}/{key}/assignments ──────────────

class TestVaultAssignments:
    def test_get_assignments_no_db_fallback(self) -> None:
        """Without db_pool, should return implicit per-agent assignment."""
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.get("/api/vault/kastor/API_KEY/assignments")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["assignments"]) == 1
        assert data["assignments"][0]["entity_type"] == "agent"
        assert data["assignments"][0]["entity_id"] == "kastor"

    def test_get_assignments_nonexistent_key_returns_404(self) -> None:
        vault = _mock_vault(agents={"kastor": {}})
        client = _make_app(secret_vault=vault)
        r = client.get("/api/vault/kastor/MISSING/assignments")
        assert r.status_code == 404


# ── PUT /api/vault/{agent_id}/{key}/assignments ──────────────

class TestVaultUpdateAssignments:
    def test_update_no_db_returns_503(self) -> None:
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.put(
            "/api/vault/kastor/API_KEY/assignments",
            json={"assignments": [{"entity_type": "agent", "entity_id": "polluks"}]},
        )
        assert r.status_code == 503

    def test_update_nonexistent_key_returns_404(self) -> None:
        vault = _mock_vault(agents={"kastor": {}})
        client = _make_app(secret_vault=vault)
        r = client.put(
            "/api/vault/kastor/MISSING/assignments",
            json={"assignments": []},
        )
        assert r.status_code == 404

    def test_update_bad_body_returns_400(self) -> None:
        vault = _mock_vault()
        client = _make_app(secret_vault=vault)
        r = client.put(
            "/api/vault/kastor/API_KEY/assignments",
            json={"assignments": "not-a-list"},
        )
        assert r.status_code == 400


# ── RBAC enforcement ─────────────────────────────────────────

class TestRbac:
    def test_forbidden_without_vault_admin(self) -> None:
        user = _FakeUser(permissions=["some.other.perm"])
        vault = _mock_vault()
        client = _make_app(user=user, secret_vault=vault)
        r = client.get("/api/vault")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self) -> None:
        vault = _mock_vault()
        client = _make_app(user=None, secret_vault=vault)
        r = client.post("/api/vault/kastor", json={"key": "K", "value": "V"})
        assert r.status_code == 401
