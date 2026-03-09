from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User
from amiagi.interfaces.web.routes.admin_routes import admin_routes


@dataclass
class _FakeAdminUser:
    email: str = "admin@example.com"
    display_name: str = "Admin"
    user_id: str = "uid"
    session_id: str = "sid"
    permissions: list[str] = field(default_factory=lambda: ["admin.users", "admin.roles", "admin.audit"])
    roles: list[str] = field(default_factory=lambda: ["admin"])


def _inject_user_middleware(user):
    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = user
            return await call_next(request)

    return _InjectUser


def _user_fixture() -> tuple[User, Role, Permission]:
    perm = Permission(id=uuid4(), codename="agents.view", description="", category="agents")
    role = Role(id=uuid4(), name="admin", description="Administrator", is_system=True, permissions=[perm])
    user = User(id=uuid4(), email="u@test.com", display_name="Test U", roles=[role])
    return user, role, perm


def _make_app(*, repo, activity_logger=None, session_manager=None) -> Starlette:
    app = Starlette(routes=list(admin_routes))
    app.state.rbac_repo = repo
    app.state.activity_logger = activity_logger
    app.state.session_manager = session_manager or AsyncMock()
    app.state.db_pool = AsyncMock()
    app.add_middleware(_inject_user_middleware(_FakeAdminUser()))
    return app


def test_user_detail_json_includes_roles_and_recent_activity() -> None:
    user, role, perm = _user_fixture()
    repo = AsyncMock()
    repo.get_user_by_id.return_value = user
    repo.list_roles.return_value = [role]
    activity_logger = SimpleNamespace(
        query=AsyncMock(return_value=[{
            "id": 1,
            "action": "admin.login",
            "detail": {"ip": "127.0.0.1"},
            "session_id": uuid4(),
            "created_at": datetime.now(timezone.utc),
        }])
    )
    client = TestClient(_make_app(repo=repo, activity_logger=activity_logger), raise_server_exceptions=False)

    response = client.get(f"/admin/users/{user.id}", headers={"accept": "application/json"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["all_roles"][0]["name"] == "admin"
    assert payload["audit_url"].endswith(str(user.id))
    assert payload["recent_activity"][0]["action"] == "admin.login"
    assert payload["permissions"] == [perm.codename]


def test_bulk_block_revokes_sessions_for_each_user() -> None:
    repo = AsyncMock()
    repo.block_user.return_value = True
    session_manager = AsyncMock()
    client = TestClient(_make_app(repo=repo, session_manager=session_manager), raise_server_exceptions=False)
    first = uuid4()
    second = uuid4()

    response = client.post("/admin/users/bulk", json={"action": "block", "user_ids": [str(first), str(second)]})

    assert response.status_code == 200
    assert response.json()["affected"] == 2
    assert session_manager.revoke_all_user_sessions.await_count == 2


def test_invite_user_falls_back_to_create_user() -> None:
    user, role, _perm = _user_fixture()

    class _Repo:
        async def create_user(self, **kwargs):
            return user

        async def assign_role(self, user_id: UUID, role_id: UUID):
            self.assigned = (user_id, role_id)

    repo = _Repo()
    client = TestClient(_make_app(repo=repo), raise_server_exceptions=False)

    response = client.post("/admin/users/invite", json={"email": "new@example.com", "role_id": str(role.id)})

    assert response.status_code == 201
    payload = response.json()
    assert payload["ok"] is True
    assert payload["user_id"] == str(user.id)
    assert repo.assigned[0] == user.id
    assert repo.assigned[1] == role.id


def test_users_template_contains_rbac_drawer_controls() -> None:
    template = Path("src/amiagi/interfaces/web/templates/admin/users.html").read_text(encoding="utf-8")

    assert 'id="btn-invite"' in template
    assert 'id="bulk-bar"' in template
    assert 'addUserRole' in template
    assert 'removeUserRole' in template
    assert 'recent_activity' in template
    assert '/admin/audit?user=' in template


def test_roles_template_contains_composer_and_export_controls() -> None:
    template = Path("src/amiagi/interfaces/web/templates/admin/roles.html").read_text(encoding="utf-8")

    assert 'id="btn-create-role"' in template
    assert 'id="btn-export-rbac-matrix"' in template
    assert 'openRoleComposer' in template
    assert 'role-diff-summary' in template


def test_permissions_template_contains_filter_and_export_controls() -> None:
    template = Path("src/amiagi/interfaces/web/templates/admin/permissions.html").read_text(encoding="utf-8")

    assert 'id="perm-filter-search"' in template
    assert 'id="btn-export-permissions-matrix"' in template
    assert '/admin/permissions/export' in template
