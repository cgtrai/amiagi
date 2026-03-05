"""Tests for RBAC models — User, Role, Permission, Page."""

from __future__ import annotations

from uuid import uuid4

from amiagi.interfaces.web.rbac.models import Page, Permission, Role, User


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

class TestPermission:
    def test_fields(self):
        p = Permission(id=uuid4(), codename="agents.view", description="View agents", category="agents")
        assert p.codename == "agents.view"
        assert p.category == "agents"


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------

class TestRole:
    def test_role_basic(self):
        r = Role(id=uuid4(), name="admin", description="Administrator", is_system=True)
        assert r.name == "admin"
        assert r.is_system is True
        assert r.permissions == []

    def test_role_with_permissions(self):
        perm = Permission(id=uuid4(), codename="agents.view", description="", category="agents")
        r = Role(id=uuid4(), name="op", description="", permissions=[perm])
        assert len(r.permissions) == 1


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class TestUser:
    def _make_user(self, **kwargs: object) -> User:
        defaults: dict[str, object] = dict(
            id=uuid4(), email="u@example.com", display_name="U", roles=[],
        )
        defaults.update(kwargs)
        return User(**defaults)  # type: ignore[arg-type]

    def test_permissions_empty_roles(self):
        u = self._make_user()
        assert u.permissions == []

    def test_permissions_from_roles(self):
        p1 = Permission(id=uuid4(), codename="a.b", description="", category="a")
        p2 = Permission(id=uuid4(), codename="c.d", description="", category="c")
        r = Role(id=uuid4(), name="r1", description="", permissions=[p1, p2])
        u = self._make_user(roles=[r])
        assert set(u.permissions) == {"a.b", "c.d"}

    def test_permissions_deduplication(self):
        p = Permission(id=uuid4(), codename="a.b", description="", category="a")
        r1 = Role(id=uuid4(), name="r1", description="", permissions=[p])
        r2 = Role(id=uuid4(), name="r2", description="", permissions=[p])
        u = self._make_user(roles=[r1, r2])
        assert u.permissions == ["a.b"]  # no duplicates

    def test_has_permission(self):
        p = Permission(id=uuid4(), codename="admin.users", description="", category="admin")
        r = Role(id=uuid4(), name="admin", description="", permissions=[p])
        u = self._make_user(roles=[r])
        assert u.has_permission("admin.users") is True
        assert u.has_permission("admin.roles") is False

    def test_has_role(self):
        r = Role(id=uuid4(), name="operator", description="")
        u = self._make_user(roles=[r])
        assert u.has_role("operator") is True
        assert u.has_role("admin") is False


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

class TestPage:
    def test_total_pages(self):
        pg = Page(items=[], total=55, page=1, per_page=20)
        assert pg.total_pages == 3

    def test_has_next(self):
        pg = Page(items=[], total=55, page=1, per_page=20)
        assert pg.has_next is True

    def test_has_no_next(self):
        pg = Page(items=[], total=55, page=3, per_page=20)
        assert pg.has_next is False

    def test_has_prev(self):
        pg = Page(items=[], total=55, page=2, per_page=20)
        assert pg.has_prev is True

    def test_has_no_prev(self):
        pg = Page(items=[], total=55, page=1, per_page=20)
        assert pg.has_prev is False
