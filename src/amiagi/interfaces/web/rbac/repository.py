"""RBAC repository — CRUD operations for users, roles, permissions.

All methods accept an ``asyncpg.Pool`` and perform atomic DB operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class RbacRepository:
    """Async repository for RBAC entities backed by PostgreSQL."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    async def list_permissions(self) -> list[Permission]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, codename, description, category FROM permissions ORDER BY category, codename"
            )
        return [Permission(id=r["id"], codename=r["codename"], description=r["description"], category=r["category"]) for r in rows]

    async def user_has_permission(self, user_id: UUID, codename: str) -> bool:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM user_roles ur
                    JOIN role_permissions rp ON rp.role_id = ur.role_id
                    JOIN permissions p ON p.id = rp.permission_id
                    WHERE ur.user_id = $1 AND p.codename = $2
                )
                """,
                user_id,
                codename,
            )

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    async def list_roles(self) -> list[Role]:
        async with self._pool.acquire() as conn:
            role_rows = await conn.fetch(
                "SELECT id, name, description, is_system FROM roles ORDER BY name"
            )
            roles: list[Role] = []
            for rr in role_rows:
                perms = await self._role_permissions(conn, rr["id"])
                roles.append(
                    Role(
                        id=rr["id"],
                        name=rr["name"],
                        description=rr["description"],
                        is_system=rr["is_system"],
                        permissions=perms,
                    )
                )
        return roles

    async def get_role(self, role_id: UUID) -> Role | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, is_system FROM roles WHERE id = $1",
                role_id,
            )
            if row is None:
                return None
            perms = await self._role_permissions(conn, row["id"])
        return Role(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_system=row["is_system"],
            permissions=perms,
        )

    async def create_role(
        self,
        name: str,
        description: str,
        permission_ids: list[UUID] | None = None,
    ) -> Role:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO roles (name, description) VALUES ($1, $2) RETURNING id, name, description, is_system",
                name,
                description,
            )
            role_id = row["id"]
            if permission_ids:
                await self._set_role_permissions(conn, role_id, permission_ids)
            perms = await self._role_permissions(conn, role_id)
        return Role(id=role_id, name=name, description=description, is_system=False, permissions=perms)

    async def update_role(
        self,
        role_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        permission_ids: list[UUID] | None = None,
    ) -> Role | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, is_system FROM roles WHERE id = $1",
                role_id,
            )
            if row is None:
                return None
            if name is not None:
                await conn.execute("UPDATE roles SET name = $1 WHERE id = $2", name, role_id)
            if description is not None:
                await conn.execute("UPDATE roles SET description = $1 WHERE id = $2", description, role_id)
            if permission_ids is not None:
                await self._set_role_permissions(conn, role_id, permission_ids)
            return await self._build_role(conn, role_id)

    async def delete_role(self, role_id: UUID) -> bool:
        """Delete a role.  Returns False if role is system-protected."""
        async with self._pool.acquire() as conn:
            is_system = await conn.fetchval(
                "SELECT is_system FROM roles WHERE id = $1", role_id
            )
            if is_system is None:
                return False
            if is_system:
                return False
            await conn.execute("DELETE FROM role_permissions WHERE role_id = $1", role_id)
            await conn.execute("DELETE FROM user_roles WHERE role_id = $1", role_id)
            await conn.execute("DELETE FROM roles WHERE id = $1", role_id)
        return True

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        async with self._pool.acquire() as conn:
            return await self._load_user(conn, "WHERE u.id = $1", user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        async with self._pool.acquire() as conn:
            return await self._load_user(conn, "WHERE u.email = $1", email)

    async def list_users(
        self,
        page: int = 1,
        per_page: int = 20,
        search: str | None = None,
    ) -> Page[User]:
        offset = (page - 1) * per_page
        where = ""
        args: list[Any] = []

        if search:
            where = "WHERE u.email ILIKE $1 OR u.display_name ILIKE $1"
            args.append(f"%{search}%")

        count_args = list(args)
        args.extend([per_page, offset])
        limit_idx = len(count_args) + 1
        offset_idx = limit_idx + 1

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM users u {where}",
                *count_args,
            )
            rows = await conn.fetch(
                f"""
                SELECT u.id, u.email, u.display_name, u.avatar_url,
                       u.provider, u.provider_sub, u.is_active, u.is_blocked,
                       u.created_at, u.updated_at
                FROM users u
                {where}
                ORDER BY u.created_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}
                """,
                *args,
            )
            users: list[User] = []
            for r in rows:
                roles = await self._user_roles(conn, r["id"])
                users.append(self._row_to_user(r, roles))

        return Page(items=users, total=total, page=page, per_page=per_page)

    async def upsert_user_from_oauth(
        self,
        email: str,
        display_name: str,
        avatar_url: str | None,
        provider: str,
        provider_sub: str,
    ) -> User:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (email, display_name, avatar_url, provider, provider_sub)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (email)
                DO UPDATE SET display_name = EXCLUDED.display_name,
                              avatar_url   = EXCLUDED.avatar_url,
                              provider_sub = EXCLUDED.provider_sub,
                              updated_at   = now()
                RETURNING id, email, display_name, avatar_url, provider, provider_sub,
                          is_active, is_blocked, created_at, updated_at
                """,
                email,
                display_name,
                avatar_url,
                provider,
                provider_sub,
            )
            roles = await self._user_roles(conn, row["id"])
        return self._row_to_user(row, roles)

    async def update_user(self, user_id: UUID, **data: Any) -> User | None:
        allowed = {"display_name", "avatar_url", "is_active", "is_blocked"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return await self.get_user_by_id(user_id)

        set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields))
        values = [user_id, *fields.values()]

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE users SET {set_clauses}, updated_at = now() WHERE id = $1",
                *values,
            )
            return await self._load_user(conn, "WHERE u.id = $1", user_id)

    async def block_user(self, user_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_blocked = true, updated_at = now() WHERE id = $1",
                user_id,
            )
        return "UPDATE 1" in (result or "")

    async def activate_user(self, user_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_blocked = false, is_active = true, updated_at = now() WHERE id = $1",
                user_id,
            )
        return "UPDATE 1" in (result or "")

    async def assign_role(self, user_id: UUID, role_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id,
                role_id,
            )

    async def remove_role(self, user_id: UUID, role_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_roles WHERE user_id = $1 AND role_id = $2",
                user_id,
                role_id,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_user(self, conn, where_clause: str, *args) -> User | None:
        row = await conn.fetchrow(
            f"""
            SELECT u.id, u.email, u.display_name, u.avatar_url,
                   u.provider, u.provider_sub, u.is_active, u.is_blocked,
                   u.created_at, u.updated_at
            FROM users u
            {where_clause}
            """,
            *args,
        )
        if row is None:
            return None
        roles = await self._user_roles(conn, row["id"])
        return self._row_to_user(row, roles)

    async def _user_roles(self, conn, user_id: UUID) -> list[Role]:
        role_rows = await conn.fetch(
            """
            SELECT r.id, r.name, r.description, r.is_system
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = $1
            ORDER BY r.name
            """,
            user_id,
        )
        roles: list[Role] = []
        for rr in role_rows:
            perms = await self._role_permissions(conn, rr["id"])
            roles.append(
                Role(id=rr["id"], name=rr["name"], description=rr["description"], is_system=rr["is_system"], permissions=perms)
            )
        return roles

    async def _role_permissions(self, conn, role_id: UUID) -> list[Permission]:
        rows = await conn.fetch(
            """
            SELECT p.id, p.codename, p.description, p.category
            FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = $1
            ORDER BY p.codename
            """,
            role_id,
        )
        return [Permission(id=r["id"], codename=r["codename"], description=r["description"], category=r["category"]) for r in rows]

    async def _set_role_permissions(self, conn, role_id: UUID, permission_ids: list[UUID]) -> None:
        await conn.execute("DELETE FROM role_permissions WHERE role_id = $1", role_id)
        for pid in permission_ids:
            await conn.execute(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                role_id,
                pid,
            )

    async def _build_role(self, conn, role_id: UUID) -> Role | None:
        row = await conn.fetchrow(
            "SELECT id, name, description, is_system FROM roles WHERE id = $1",
            role_id,
        )
        if row is None:
            return None
        perms = await self._role_permissions(conn, row["id"])
        return Role(id=row["id"], name=row["name"], description=row["description"], is_system=row["is_system"], permissions=perms)

    @staticmethod
    def _row_to_user(row, roles: list[Role]) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            avatar_url=row["avatar_url"],
            provider=row["provider"],
            provider_sub=row.get("provider_sub", ""),
            is_active=row["is_active"],
            is_blocked=row["is_blocked"],
            roles=roles,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
