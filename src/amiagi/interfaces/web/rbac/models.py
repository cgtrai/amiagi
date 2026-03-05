"""RBAC domain models — User, Role, Permission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

T = TypeVar("T")


@dataclass(frozen=True)
class Permission:
    """Single permission entry (e.g. ``agents.view``)."""

    id: UUID
    codename: str
    description: str
    category: str


@dataclass(frozen=True)
class Role:
    """Named set of permissions."""

    id: UUID
    name: str
    description: str
    is_system: bool = False
    permissions: list[Permission] = field(default_factory=list)


@dataclass(frozen=True)
class User:
    """Application user with roles and permissions."""

    id: UUID
    email: str
    display_name: str
    avatar_url: str | None = None
    provider: str = "google"
    provider_sub: str = ""
    is_active: bool = True
    is_blocked: bool = False
    roles: list[Role] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def permissions(self) -> list[str]:
        """Flat list of unique permission codenames from all roles."""
        seen: set[str] = set()
        result: list[str] = []
        for role in self.roles:
            for perm in role.permissions:
                if perm.codename not in seen:
                    seen.add(perm.codename)
                    result.append(perm.codename)
        return result

    def has_permission(self, codename: str) -> bool:
        return codename in self.permissions

    def has_role(self, name: str) -> bool:
        return any(r.name == name for r in self.roles)


@dataclass(frozen=True)
class Page(Generic[T]):
    """Paginated result set."""

    items: list[T]
    total: int
    page: int
    per_page: int

    @property
    def total_pages(self) -> int:
        if self.per_page <= 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1
