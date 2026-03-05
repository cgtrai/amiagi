"""Tests for RbacService — service layer delegating to mocked RbacRepository."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User
from amiagi.interfaces.web.rbac.service import RbacService


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_user(email: str = "alice@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        display_name="Alice",
        provider="google",
        provider_sub="sub1",
    )


def _make_role(name: str = "editor") -> Role:
    return Role(
        id=uuid4(),
        name=name,
        description=f"Role {name}",
    )


def _make_permission(codename: str = "agents.view") -> Permission:
    return Permission(
        id=uuid4(),
        codename=codename,
        description=f"Can {codename}",
        category=codename.split(".")[0],
    )


def _make_service() -> tuple[RbacService, MagicMock]:
    repo = MagicMock()
    # Make all methods async
    for method_name in [
        "get_user_by_id", "get_user_by_email", "list_users",
        "update_user", "block_user", "activate_user",
        "list_roles", "get_role", "create_role", "delete_role",
        "list_permissions", "user_has_permission",
        "assign_role", "remove_role",
    ]:
        setattr(repo, method_name, AsyncMock())
    service = RbacService(repo)
    return service, repo


# ------------------------------------------------------------------
# User tests
# ------------------------------------------------------------------

class TestUserOperations:

    @pytest.mark.asyncio
    async def test_get_user(self) -> None:
        svc, repo = _make_service()
        expected = _make_user()
        repo.get_user_by_id.return_value = expected
        result = await svc.get_user(expected.id)
        assert result == expected
        repo.get_user_by_id.assert_awaited_once_with(expected.id)

    @pytest.mark.asyncio
    async def test_get_user_not_found(self) -> None:
        svc, repo = _make_service()
        repo.get_user_by_id.return_value = None
        assert await svc.get_user(uuid4()) is None

    @pytest.mark.asyncio
    async def test_get_user_by_email(self) -> None:
        svc, repo = _make_service()
        user = _make_user("bob@test.com")
        repo.get_user_by_email.return_value = user
        result = await svc.get_user_by_email("bob@test.com")
        assert result is not None
        assert result.email == "bob@test.com"

    @pytest.mark.asyncio
    async def test_list_users(self) -> None:
        svc, repo = _make_service()
        page = Page(items=[_make_user()], total=1, page=1, per_page=20)
        repo.list_users.return_value = page
        result = await svc.list_users()
        assert result.total == 1
        repo.list_users.assert_awaited_once_with(page=1, per_page=20, search=None)

    @pytest.mark.asyncio
    async def test_list_users_with_search(self) -> None:
        svc, repo = _make_service()
        repo.list_users.return_value = Page(items=[], total=0, page=1, per_page=20)
        await svc.list_users(search="foo")
        repo.list_users.assert_awaited_once_with(page=1, per_page=20, search="foo")

    @pytest.mark.asyncio
    async def test_update_user(self) -> None:
        svc, repo = _make_service()
        user = _make_user()
        repo.update_user.return_value = user
        result = await svc.update_user(user.id, display_name="Bob")
        assert result == user

    @pytest.mark.asyncio
    async def test_block_user(self) -> None:
        svc, repo = _make_service()
        repo.block_user.return_value = True
        assert await svc.block_user(uuid4()) is True

    @pytest.mark.asyncio
    async def test_activate_user(self) -> None:
        svc, repo = _make_service()
        repo.activate_user.return_value = True
        assert await svc.activate_user(uuid4()) is True


# ------------------------------------------------------------------
# Role tests
# ------------------------------------------------------------------

class TestRoleOperations:

    @pytest.mark.asyncio
    async def test_list_roles(self) -> None:
        svc, repo = _make_service()
        roles = [_make_role("admin"), _make_role("viewer")]
        repo.list_roles.return_value = roles
        result = await svc.list_roles()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_role(self) -> None:
        svc, repo = _make_service()
        role = _make_role("editor")
        repo.get_role.return_value = role
        result = await svc.get_role(role.id)
        assert result is not None
        assert result.name == "editor"

    @pytest.mark.asyncio
    async def test_create_role(self) -> None:
        svc, repo = _make_service()
        role = _make_role("new-role")
        repo.create_role.return_value = role
        result = await svc.create_role("new-role", "desc")
        assert result.name == "new-role"
        repo.create_role.assert_awaited_once_with("new-role", "desc", None)

    @pytest.mark.asyncio
    async def test_delete_role(self) -> None:
        svc, repo = _make_service()
        repo.delete_role.return_value = True
        assert await svc.delete_role(uuid4()) is True


# ------------------------------------------------------------------
# Permission tests
# ------------------------------------------------------------------

class TestPermissionOperations:

    @pytest.mark.asyncio
    async def test_list_permissions(self) -> None:
        svc, repo = _make_service()
        repo.list_permissions.return_value = [_make_permission()]
        result = await svc.list_permissions()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_check_permission_true(self) -> None:
        svc, repo = _make_service()
        repo.user_has_permission.return_value = True
        assert await svc.check_permission(uuid4(), "agents.view") is True

    @pytest.mark.asyncio
    async def test_check_permission_false(self) -> None:
        svc, repo = _make_service()
        repo.user_has_permission.return_value = False
        assert await svc.check_permission(uuid4(), "agents.delete") is False


# ------------------------------------------------------------------
# Role assignment tests
# ------------------------------------------------------------------

class TestRoleAssignment:

    @pytest.mark.asyncio
    async def test_assign_role(self) -> None:
        svc, repo = _make_service()
        uid, rid = uuid4(), uuid4()
        await svc.assign_role(uid, rid)
        repo.assign_role.assert_awaited_once_with(uid, rid)

    @pytest.mark.asyncio
    async def test_remove_role(self) -> None:
        svc, repo = _make_service()
        uid, rid = uuid4(), uuid4()
        await svc.remove_role(uid, rid)
        repo.remove_role.assert_awaited_once_with(uid, rid)
