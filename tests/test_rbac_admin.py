"""Tests for require_permission decorator and admin routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.rbac.middleware import require_permission
from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeUser:
    email: str = "admin@example.com"
    display_name: str = "Admin"
    user_id: str = "uid"
    session_id: str = "sid"
    permissions: list[str] = field(default_factory=lambda: ["admin.users", "admin.roles"])
    roles: list[str] = field(default_factory=lambda: ["admin"])


@require_permission("admin.users")
async def _protected(request: Request):
    return PlainTextResponse("ok")


@require_permission("admin.users", "admin.roles")
async def _multi_perm(request: Request):
    return PlainTextResponse("multi-ok")


def _make_app_with_state(*, user=None):
    """Create a minimal Starlette app.  Pre-populate request.state.user via middleware."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if user is not None:
                request.state.user = user
            return await call_next(request)

    app = Starlette(routes=[
        Route("/test", _protected),
        Route("/multi", _multi_perm),
    ])
    app.add_middleware(_InjectUser)
    return app


# ---------------------------------------------------------------------------
# Tests — require_permission
# ---------------------------------------------------------------------------

class TestRequirePermission:
    def test_401_no_user(self):
        client = TestClient(_make_app_with_state(user=None))
        r = client.get("/test")
        assert r.status_code == 401

    def test_403_missing_perm(self):
        client = TestClient(_make_app_with_state(user=_FakeUser(permissions=["other.perm"])))
        r = client.get("/test")
        assert r.status_code == 403
        assert "admin.users" in r.json()["detail"]

    def test_200_with_perm(self):
        client = TestClient(_make_app_with_state(user=_FakeUser()))
        r = client.get("/test")
        assert r.status_code == 200
        assert r.text == "ok"

    def test_multi_perm_all_present(self):
        client = TestClient(_make_app_with_state(user=_FakeUser()))
        r = client.get("/multi")
        assert r.status_code == 200

    def test_multi_perm_partial(self):
        client = TestClient(_make_app_with_state(user=_FakeUser(permissions=["admin.users"])))
        r = client.get("/multi")
        assert r.status_code == 403
        assert "admin.roles" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Tests — Admin routes (JSON API mode, no templates)
# ---------------------------------------------------------------------------

def _fake_rbac_repo():
    repo = AsyncMock()
    uid = uuid4()
    perm = Permission(id=uuid4(), codename="agents.view", description="", category="agents")
    role = Role(id=uuid4(), name="admin", description="Administrator", is_system=True, permissions=[perm])
    user = User(id=uid, email="u@test.com", display_name="Test U", roles=[role])
    page = Page(items=[user], total=1, page=1, per_page=20)

    repo.list_users.return_value = page
    repo.get_user_by_id.return_value = user
    repo.list_roles.return_value = [role]
    repo.list_permissions.return_value = [perm]
    repo.create_role.return_value = Role(id=uuid4(), name="new", description="New role")
    repo.update_role.return_value = role
    repo.delete_role.return_value = True
    repo.block_user.return_value = True
    repo.activate_user.return_value = True
    return repo


def _make_admin_app(rbac_repo=None, session_manager=None, db_pool=None):
    from starlette.middleware.base import BaseHTTPMiddleware
    from amiagi.interfaces.web.routes.admin_routes import admin_routes

    admin_user = _FakeUser(permissions=["admin.users", "admin.roles", "admin.audit"])

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = admin_user
            return await call_next(request)

    app = Starlette(routes=list(admin_routes))
    app.state.rbac_repo = rbac_repo or _fake_rbac_repo()
    app.state.session_manager = session_manager or AsyncMock()
    app.state.db_pool = db_pool or MagicMock()
    app.add_middleware(_InjectUser)
    return app


class TestAdminUsers:
    def test_list_users_json(self):
        client = TestClient(_make_admin_app())
        r = client.get("/admin/users", headers={"accept": "application/json"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["email"] == "u@test.com"

    def test_user_detail_json(self):
        client = TestClient(_make_admin_app())
        uid = uuid4()
        r = client.get(f"/admin/users/{uid}")
        assert r.status_code == 200

    def test_user_not_found(self):
        repo = _fake_rbac_repo()
        repo.get_user_by_id.return_value = None
        client = TestClient(_make_admin_app(rbac_repo=repo))
        r = client.get(f"/admin/users/{uuid4()}")
        assert r.status_code == 404

    def test_update_user_roles(self):
        repo = _fake_rbac_repo()
        client = TestClient(_make_admin_app(rbac_repo=repo))
        uid = repo.get_user_by_id.return_value.id
        r = client.post(
            f"/admin/users/{uid}/roles",
            json={"role_ids": [str(uuid4())]},
        )
        assert r.status_code == 200

    def test_block_user(self):
        repo = _fake_rbac_repo()
        sm = AsyncMock()
        client = TestClient(_make_admin_app(rbac_repo=repo, session_manager=sm))
        r = client.post(f"/admin/users/{uuid4()}/block")
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"
        sm.revoke_all_user_sessions.assert_awaited_once()

    def test_activate_user(self):
        client = TestClient(_make_admin_app())
        r = client.post(f"/admin/users/{uuid4()}/activate")
        assert r.status_code == 200
        assert r.json()["status"] == "activated"


class TestAdminRoles:
    def test_list_roles_json(self):
        client = TestClient(_make_admin_app())
        r = client.get("/admin/roles")
        assert r.status_code == 200
        assert len(r.json()["roles"]) == 1

    def test_role_detail_json(self):
        repo = _fake_rbac_repo()
        role = repo.list_roles.return_value[0]
        client = TestClient(_make_admin_app(rbac_repo=repo))
        r = client.get(f"/admin/roles/{role.id}")
        assert r.status_code == 200
        payload = r.json()
        assert payload["role"]["name"] == role.name
        assert payload["all_permissions"][0]["codename"] == role.permissions[0].codename

    def test_create_role(self):
        client = TestClient(_make_admin_app())
        r = client.post("/admin/roles", json={"name": "analyst", "description": "Data analyst"})
        assert r.status_code == 201

    def test_create_role_no_name(self):
        client = TestClient(_make_admin_app())
        r = client.post("/admin/roles", json={"name": "", "description": ""})
        assert r.status_code == 400

    def test_update_role(self):
        client = TestClient(_make_admin_app())
        r = client.put(f"/admin/roles/{uuid4()}", json={"name": "renamed"})
        assert r.status_code == 200

    def test_delete_role(self):
        client = TestClient(_make_admin_app())
        r = client.delete(f"/admin/roles/{uuid4()}")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

    def test_delete_system_role_fails(self):
        repo = _fake_rbac_repo()
        repo.delete_role.return_value = False
        client = TestClient(_make_admin_app(rbac_repo=repo))
        r = client.delete(f"/admin/roles/{uuid4()}")
        assert r.status_code == 400
        assert "cannot_delete" in r.json()["error"]


class TestAdminPermissions:
    def test_list_permissions(self):
        client = TestClient(_make_admin_app())
        r = client.get("/admin/permissions")
        assert r.status_code == 200
        assert len(r.json()["permissions"]) == 1

    def test_export_permissions_matrix_csv(self):
        client = TestClient(_make_admin_app())
        r = client.get("/admin/permissions/export?format=csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "permission,category,description" in r.text


class TestAdminAudit:
    def test_audit_log_json(self):
        pool = MagicMock()
        fake_conn = AsyncMock()
        fake_conn.fetchval.return_value = 0
        fake_conn.fetch.return_value = []
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        client = TestClient(_make_admin_app(db_pool=pool))
        r = client.get("/admin/audit")
        assert r.status_code == 200
        assert r.json()["total"] == 0
