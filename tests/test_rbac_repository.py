"""Tests for RbacRepository — async CRUD with mocked asyncpg pool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User
from amiagi.interfaces.web.rbac.repository import RbacRepository


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _perm_row(codename: str = "agents.view", category: str = "agents") -> dict:
    return {"id": uuid4(), "codename": codename, "description": f"Can {codename}", "category": category}


def _role_row(name: str = "admin", is_system: bool = False) -> dict:
    return {"id": uuid4(), "name": name, "description": f"Role {name}", "is_system": is_system}


def _user_row(email: str = "a@b.com") -> dict:
    uid = uuid4()
    return {
        "id": uid, "email": email, "display_name": "Alice",
        "avatar_url": None, "provider": "google", "provider_sub": "sub1",
        "is_active": True, "is_blocked": False,
        "created_at": None, "updated_at": None,
    }


class _FakeAcquire:
    """Async context manager that returns a mock connection."""

    def __init__(self, conn: AsyncMock) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return False


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    """Return (pool, conn) where pool.acquire() yields conn."""
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _FakeAcquire(conn)
    return pool, conn


# ------------------------------------------------------------------
# Permission tests
# ------------------------------------------------------------------

class TestPermissions:

    @pytest.mark.asyncio
    async def test_list_permissions_empty(self) -> None:
        pool, conn = _make_pool()
        conn.fetch.return_value = []
        repo = RbacRepository(pool)
        result = await repo.list_permissions()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_permissions_returns_models(self) -> None:
        pool, conn = _make_pool()
        row = _perm_row()
        conn.fetch.return_value = [row]
        repo = RbacRepository(pool)
        result = await repo.list_permissions()
        assert len(result) == 1
        assert isinstance(result[0], Permission)
        assert result[0].codename == row["codename"]

    @pytest.mark.asyncio
    async def test_user_has_permission_true(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = True
        repo = RbacRepository(pool)
        assert await repo.user_has_permission(uuid4(), "agents.view") is True

    @pytest.mark.asyncio
    async def test_user_has_permission_false(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = False
        repo = RbacRepository(pool)
        assert await repo.user_has_permission(uuid4(), "agents.view") is False


# ------------------------------------------------------------------
# Role tests
# ------------------------------------------------------------------

class TestRoles:

    @pytest.mark.asyncio
    async def test_list_roles_empty(self) -> None:
        pool, conn = _make_pool()
        conn.fetch.return_value = []
        repo = RbacRepository(pool)
        assert await repo.list_roles() == []

    @pytest.mark.asyncio
    async def test_list_roles_builds_models(self) -> None:
        pool, conn = _make_pool()
        rr = _role_row("editor")
        conn.fetch.side_effect = [
            [rr],         # role rows
            [],           # permissions for that role
        ]
        repo = RbacRepository(pool)
        roles = await repo.list_roles()
        assert len(roles) == 1
        assert roles[0].name == "editor"

    @pytest.mark.asyncio
    async def test_get_role_found(self) -> None:
        pool, conn = _make_pool()
        rr = _role_row("viewer")
        conn.fetchrow.return_value = rr
        conn.fetch.return_value = []  # permissions
        repo = RbacRepository(pool)
        role = await repo.get_role(rr["id"])
        assert role is not None
        assert role.name == "viewer"

    @pytest.mark.asyncio
    async def test_get_role_not_found(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = None
        repo = RbacRepository(pool)
        assert await repo.get_role(uuid4()) is None

    @pytest.mark.asyncio
    async def test_create_role(self) -> None:
        pool, conn = _make_pool()
        rr = _role_row("new_role")
        conn.fetchrow.return_value = rr
        conn.fetch.return_value = []  # permissions
        repo = RbacRepository(pool)
        role = await repo.create_role("new_role", "A new role")
        assert role.name == "new_role"

    @pytest.mark.asyncio
    async def test_delete_role_system_protected(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = True  # is_system
        repo = RbacRepository(pool)
        assert await repo.delete_role(uuid4()) is False

    @pytest.mark.asyncio
    async def test_delete_role_success(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = False  # not system
        repo = RbacRepository(pool)
        assert await repo.delete_role(uuid4()) is True


# ------------------------------------------------------------------
# User tests
# ------------------------------------------------------------------

class TestUsers:

    @pytest.mark.asyncio
    async def test_get_user_by_id(self) -> None:
        pool, conn = _make_pool()
        ur = _user_row()
        conn.fetchrow.return_value = ur
        conn.fetch.return_value = []  # roles
        repo = RbacRepository(pool)
        user = await repo.get_user_by_id(ur["id"])
        assert user is not None
        assert user.email == "a@b.com"

    @pytest.mark.asyncio
    async def test_get_user_by_email(self) -> None:
        pool, conn = _make_pool()
        ur = _user_row("bob@example.com")
        conn.fetchrow.return_value = ur
        conn.fetch.return_value = []
        repo = RbacRepository(pool)
        user = await repo.get_user_by_email("bob@example.com")
        assert user is not None
        assert user.email == "bob@example.com"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = None
        repo = RbacRepository(pool)
        assert await repo.get_user_by_id(uuid4()) is None

    @pytest.mark.asyncio
    async def test_list_users(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = 1
        ur = _user_row()
        conn.fetch.side_effect = [
            [ur],   # user rows
            [],     # roles for user
        ]
        repo = RbacRepository(pool)
        page = await repo.list_users()
        assert isinstance(page, Page)
        assert page.total == 1
        assert len(page.items) == 1

    @pytest.mark.asyncio
    async def test_list_users_with_search(self) -> None:
        pool, conn = _make_pool()
        conn.fetchval.return_value = 0
        conn.fetch.return_value = []
        repo = RbacRepository(pool)
        page = await repo.list_users(search="xyz")
        assert page.total == 0
        assert page.items == []

    @pytest.mark.asyncio
    async def test_block_user(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 1"
        repo = RbacRepository(pool)
        assert await repo.block_user(uuid4()) is True

    @pytest.mark.asyncio
    async def test_block_user_not_found(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 0"
        repo = RbacRepository(pool)
        assert await repo.block_user(uuid4()) is False

    @pytest.mark.asyncio
    async def test_activate_user(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 1"
        repo = RbacRepository(pool)
        assert await repo.activate_user(uuid4()) is True

    @pytest.mark.asyncio
    async def test_assign_role(self) -> None:
        pool, conn = _make_pool()
        repo = RbacRepository(pool)
        await repo.assign_role(uuid4(), uuid4())
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_role(self) -> None:
        pool, conn = _make_pool()
        repo = RbacRepository(pool)
        await repo.remove_role(uuid4(), uuid4())
        conn.execute.assert_called_once()
