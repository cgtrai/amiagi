"""RBAC service layer — high-level operations delegating to RbacRepository.

Provides a clean API for route handlers.  Encapsulates business logic
(e.g. "cannot delete system roles") that the repository handles at SQL
level, but is also enforced here for clarity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User

if TYPE_CHECKING:
    from amiagi.interfaces.web.rbac.repository import RbacRepository

logger = logging.getLogger(__name__)


class RbacService:
    """High-level RBAC operations backed by :class:`RbacRepository`."""

    def __init__(self, repository: "RbacRepository") -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def get_user(self, user_id: UUID) -> User | None:
        """Fetch user by ID."""
        return await self._repo.get_user_by_id(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        """Fetch user by e-mail address."""
        return await self._repo.get_user_by_email(email)

    async def list_users(
        self,
        page: int = 1,
        per_page: int = 20,
        search: str | None = None,
    ) -> Page[User]:
        """Paginated user listing with optional search."""
        return await self._repo.list_users(page=page, per_page=per_page, search=search)

    async def update_user(self, user_id: UUID, **data: Any) -> User | None:
        """Update mutable user attributes."""
        return await self._repo.update_user(user_id, **data)

    async def block_user(self, user_id: UUID) -> bool:
        """Block a user account."""
        return await self._repo.block_user(user_id)

    async def activate_user(self, user_id: UUID) -> bool:
        """Activate (unblock) a user account."""
        return await self._repo.activate_user(user_id)

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    async def list_roles(self) -> list[Role]:
        return await self._repo.list_roles()

    async def get_role(self, role_id: UUID) -> Role | None:
        return await self._repo.get_role(role_id)

    async def create_role(
        self,
        name: str,
        description: str,
        permission_ids: list[UUID] | None = None,
    ) -> Role:
        """Create a new custom role."""
        return await self._repo.create_role(name, description, permission_ids)

    async def delete_role(self, role_id: UUID) -> bool:
        """Delete a role.  System roles cannot be deleted."""
        return await self._repo.delete_role(role_id)

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    async def list_permissions(self) -> list[Permission]:
        return await self._repo.list_permissions()

    async def check_permission(self, user_id: UUID, codename: str) -> bool:
        """Return True if the user has the given permission."""
        return await self._repo.user_has_permission(user_id, codename)

    # ------------------------------------------------------------------
    # Role assignment
    # ------------------------------------------------------------------

    async def assign_role(self, user_id: UUID, role_id: UUID) -> None:
        """Assign a role to a user (idempotent)."""
        await self._repo.assign_role(user_id, role_id)

    async def remove_role(self, user_id: UUID, role_id: UUID) -> None:
        """Remove a role from a user."""
        await self._repo.remove_role(user_id, role_id)
